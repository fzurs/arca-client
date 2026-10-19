"""Cliente de WSFEv1 (Factura Electrónica)."""

from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

import zeep

from ..certificates import cuit_from_certificate
from ..environments import wsfe_wsdl
from ..exceptions import ArcaError, WSFEError
from ..transport import make_transport
from ..wsaa import WSAA
from .types import (
    CbteTipo,
    ComprobanteAutorizado,
    Concepto,
    CondicionIVAReceptor,
    DocTipo,
    Mensaje,
)

SERVICE = "wsfe"
# Argentina no usa horario de verano: UTC-3 fijo.
ARGENTINA_TZ = timezone(timedelta(hours=-3))
_CENTS = Decimal("0.01")


class WSFE:
    """Emite comprobantes electrónicos con CAE.

    >>> wsaa = WSAA("cert.crt", "private.key", Environment.HOMOLOGACION)
    >>> wsfe = WSFE(wsaa)
    >>> factura = wsfe.create_invoice_c(punto_venta=1, importe_total="1500.00")
    >>> factura.numero, factura.cae
    """

    def __init__(self, wsaa: WSAA, cuit: str | int | None = None, *, timeout: float = 30) -> None:
        self.wsaa = wsaa
        self.env = wsaa.env
        cuit = cuit or cuit_from_certificate(wsaa.certificate)
        if not cuit:
            raise ArcaError("No se pudo obtener la CUIT del certificado; pasala con `cuit=`")
        self.cuit = int(cuit)
        self.timeout = timeout
        self._soap: zeep.Client | None = None

    # --- Métodos de negocio --------------------------------------------------

    def last_voucher_number(self, punto_venta: int, tipo: CbteTipo) -> int:
        """Último número autorizado para el punto de venta y tipo (0 si no hay ninguno)."""
        result = self._service().FECompUltimoAutorizado(
            Auth=self._auth(), PtoVta=punto_venta, CbteTipo=int(tipo)
        )
        errores = _mensajes(result, "Errors", "Err")
        if errores:
            raise WSFEError(errores)
        return int(result.CbteNro)

    def create_invoice_c(
        self,
        punto_venta: int,
        importe_total: Decimal | int | str,
        *,
        concepto: Concepto = Concepto.PRODUCTOS,
        doc_tipo: DocTipo = DocTipo.CONSUMIDOR_FINAL,
        doc_nro: int = 0,
        condicion_iva_receptor: CondicionIVAReceptor | None = None,
        fecha: date | None = None,
        servicio_desde: date | None = None,
        servicio_hasta: date | None = None,
        vencimiento_pago: date | None = None,
    ) -> ComprobanteAutorizado:
        """Autoriza una Factura C (monotributistas) y devuelve el CAE.

        El número se calcula como el último autorizado + 1. En clase C no se
        discrimina IVA: el importe total es igual al neto (subtotal).

        - Consumidor final sin identificar: ``doc_tipo=DocTipo.CONSUMIDOR_FINAL``
          (por defecto), ``doc_nro=0``.
        - Receptor identificado: ``doc_tipo`` CUIT/CUIL/DNI, su número y la
          ``condicion_iva_receptor`` (obligatoria en ese caso).
        - Servicios (concepto 2 o 3): ``servicio_desde`` y ``servicio_hasta``
          son obligatorios; ``vencimiento_pago`` por defecto es la fecha de emisión.
        """
        total = _amount(importe_total)
        fecha = fecha or datetime.now(ARGENTINA_TZ).date()
        condicion = _condicion_iva(doc_tipo, doc_nro, condicion_iva_receptor)
        concepto = Concepto(concepto)

        detalle = {
            "Concepto": int(concepto),
            "DocTipo": int(doc_tipo),
            "DocNro": int(doc_nro),
            "CbteFch": _fmt(fecha),
            "ImpTotal": float(total),
            "ImpTotConc": 0,
            "ImpNeto": float(total),
            "ImpOpEx": 0,
            "ImpTrib": 0,
            "ImpIVA": 0,
            "MonId": "PES",
            "MonCotiz": 1,
            "CondicionIVAReceptorId": int(condicion),
        }
        if concepto is not Concepto.PRODUCTOS:
            if not (servicio_desde and servicio_hasta):
                raise ValueError("Para servicios hay que indicar servicio_desde y servicio_hasta")
            if servicio_hasta < servicio_desde:
                raise ValueError("servicio_hasta no puede ser anterior a servicio_desde")
            vencimiento_pago = vencimiento_pago or fecha
            if vencimiento_pago < fecha:
                raise ValueError("vencimiento_pago no puede ser anterior a la fecha de emisión")
            detalle |= {
                "FchServDesde": _fmt(servicio_desde),
                "FchServHasta": _fmt(servicio_hasta),
                "FchVtoPago": _fmt(vencimiento_pago),
            }

        tipo = CbteTipo.FACTURA_C
        numero = self.last_voucher_number(punto_venta, tipo) + 1
        detalle |= {"CbteDesde": numero, "CbteHasta": numero}

        result = self._service().FECAESolicitar(
            Auth=self._auth(),
            FeCAEReq={
                "FeCabReq": {"CantReg": 1, "PtoVta": punto_venta, "CbteTipo": int(tipo)},
                "FeDetReq": {"FECAEDetRequest": [detalle]},
            },
        )
        return _parse_cae_response(
            result,
            cuit=self.cuit,
            punto_venta=punto_venta,
            tipo=tipo,
            importe_total=total,
            concepto=concepto,
            doc_tipo=DocTipo(doc_tipo),
            doc_nro=int(doc_nro),
            condicion_iva_receptor=condicion,
            servicio_desde=servicio_desde if concepto is not Concepto.PRODUCTOS else None,
            servicio_hasta=servicio_hasta if concepto is not Concepto.PRODUCTOS else None,
            vencimiento_pago=vencimiento_pago if concepto is not Concepto.PRODUCTOS else None,
        )

    # --- Infraestructura -----------------------------------------------------

    def _auth(self) -> dict:
        ticket = self.wsaa.get_ticket(SERVICE)
        return {"Token": ticket.token, "Sign": ticket.sign, "Cuit": self.cuit}

    def _service(self):
        if self._soap is None:
            transport = make_transport(self.env, self.timeout)
            self._soap = zeep.Client(wsfe_wsdl(self.env), transport=transport)
        return self._soap.service


def _parse_cae_response(result, **datos) -> ComprobanteAutorizado:
    errores = _mensajes(result, "Errors", "Err")
    eventos = _mensajes(result, "Events", "Evt")
    detalles = getattr(getattr(result, "FeDetResp", None), "FEDetResponse", None) or []
    det = detalles[0] if detalles else None
    observaciones = _mensajes(det, "Obs", "Observaciones") if det is not None else []

    if det is None or det.Resultado != "A" or errores:
        raise WSFEError(errores, observaciones)

    return ComprobanteAutorizado(
        numero=int(det.CbteDesde),
        fecha=_parse_date(det.CbteFch),
        cae=str(det.CAE),
        cae_vencimiento=_parse_date(det.CAEFchVto),
        observaciones=observaciones,
        eventos=eventos,
        **datos,
    )


def _mensajes(obj, container: str, item: str) -> list[Mensaje]:
    items = getattr(getattr(obj, container, None), item, None) or []
    return [Mensaje(int(m.Code), str(m.Msg)) for m in items]


def _condicion_iva(
    doc_tipo: DocTipo, doc_nro: int, condicion: CondicionIVAReceptor | None
) -> CondicionIVAReceptor:
    doc_tipo = DocTipo(doc_tipo)
    if doc_tipo is DocTipo.CONSUMIDOR_FINAL:
        if doc_nro != 0:
            raise ValueError("Con DocTipo 99 (consumidor final) doc_nro debe ser 0")
        return CondicionIVAReceptor(condicion or CondicionIVAReceptor.CONSUMIDOR_FINAL)
    if doc_nro <= 0:
        raise ValueError(f"Falta el número de documento para DocTipo {doc_tipo.name}")
    if condicion is None:
        raise ValueError("Con un receptor identificado hay que indicar condicion_iva_receptor")
    return CondicionIVAReceptor(condicion)


def _amount(value: Decimal | int | str) -> Decimal:
    if isinstance(value, float):
        raise TypeError("Usá Decimal, int o str para los importes, no float")
    try:
        amount = Decimal(value)
    except InvalidOperation as e:
        raise ValueError(f"Importe inválido: {value!r}") from e
    if amount <= 0:
        raise ValueError("El importe total debe ser mayor a 0")
    if amount != amount.quantize(_CENTS, rounding=ROUND_HALF_EVEN):
        raise ValueError("El importe admite como máximo 2 decimales")
    return amount.quantize(_CENTS)


def _fmt(d: date) -> str:
    return d.strftime("%Y%m%d")


def _parse_date(value: str) -> date:
    return datetime.strptime(str(value), "%Y%m%d").date()
