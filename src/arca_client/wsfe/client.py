"""Cliente de WSFEv1 (Factura Electrónica)."""

import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal

import requests
import zeep
from lxml.etree import XMLSyntaxError
from zeep.exceptions import Fault, TransportError, XMLParseError

from ..certificates import cuit_from_certificate, is_valid_cuit
from ..environments import wsfe_wsdl
from ..exceptions import ArcaError, WSFEConnectionError, WSFEError
from ..transport import make_transport
from ..wsaa import WSAA
from .types import (
    Alicuota,
    CbteTipo,
    ComprobanteAsociado,
    ComprobanteAutorizado,
    Concepto,
    CondicionIVAReceptor,
    DocTipo,
    IvaAlicuota,
    Mensaje,
    TipoTributo,
    Tributo,
    _monto,
)

SERVICE = "wsfe"
# Argentina no usa horario de verano: UTC-3 fijo.
ARGENTINA_TZ = timezone(timedelta(hours=-3))
_CENTS = Decimal("0.01")
NOT_FOUND = 602  # "No existen datos en nuestros registros"
# "El numero o fecha del comprobante no se corresponde con el proximo a autorizar"
NEXT_NUMBER_MISMATCH = 10016

# Condiciones del receptor por clase (FEParamGetCondicionIvaReceptor). Desde la RG 5003
# a los monotributistas se les emite A; la B queda para quienes no computan el IVA.
CONDICIONES_A = frozenset(
    {
        CondicionIVAReceptor.IVA_RESPONSABLE_INSCRIPTO,
        CondicionIVAReceptor.RESPONSABLE_MONOTRIBUTO,
        CondicionIVAReceptor.MONOTRIBUTISTA_SOCIAL,
        CondicionIVAReceptor.MONOTRIBUTO_TRABAJADOR_INDEPENDIENTE_PROMOVIDO,
    }
)
CONDICIONES_B = frozenset(CondicionIVAReceptor) - CONDICIONES_A

# Errores después de los cuales no se sabe si ARCA procesó el pedido.
_COMMUNICATION_ERRORS = (
    requests.RequestException,
    TransportError,
    Fault,
    XMLParseError,
    XMLSyntaxError,
)


class _UnrecognizedResponse(ArcaError):
    pass


class WSFE:
    """Emite comprobantes electrónicos con CAE.

    >>> wsaa = WSAA("cert.crt", "private.key", Environment.HOMOLOGACION)
    >>> wsfe = WSFE(wsaa)
    >>> factura = wsfe.create_invoice_c(punto_venta=1, importe_total="1500.00")
    >>> factura = wsfe.create_invoice_a(
    ...     punto_venta=1,
    ...     doc_nro=30712345671,
    ...     condicion_iva_receptor=CondicionIVAReceptor.IVA_RESPONSABLE_INSCRIPTO,
    ...     neto={Alicuota.IVA_21: "1000.00"},
    ... )
    >>> factura.numero, factura.cae
    """

    def __init__(
        self,
        wsaa: WSAA,
        cuit: str | int | None = None,
        *,
        timeout: float = 30,
        recovery_attempts: int = 3,
        recovery_delay: float = 2.0,
        numbering_attempts: int = 3,
    ) -> None:
        self.wsaa = wsaa
        self.env = wsaa.env
        cuit = cuit or cuit_from_certificate(wsaa.certificate)
        if not cuit:
            raise ArcaError("No se pudo obtener la CUIT del certificado; pasala con `cuit=`")
        self.cuit = int(cuit)
        self.timeout = timeout
        self.recovery_attempts = recovery_attempts
        self.recovery_delay = recovery_delay
        self.numbering_attempts = numbering_attempts
        self._soap: zeep.Client | None = None

    # --- Consultas -----------------------------------------------------------

    def last_voucher_number(self, punto_venta: int, tipo: CbteTipo) -> int:
        """Último número autorizado para el punto de venta y tipo (0 si no hay ninguno)."""
        result = self._service().FECompUltimoAutorizado(
            Auth=self._auth(), PtoVta=punto_venta, CbteTipo=int(tipo)
        )
        errores = _mensajes(result, "Errors", "Err")
        if errores:
            raise WSFEError(errores)
        return int(result.CbteNro)

    def get_voucher(
        self, punto_venta: int, tipo: CbteTipo, numero: int
    ) -> ComprobanteAutorizado | None:
        """Consulta un comprobante emitido (FECompConsultar). ``None`` si ARCA no lo registra."""
        result = self._service().FECompConsultar(
            Auth=self._auth(),
            FeCompConsReq={"CbteTipo": int(tipo), "CbteNro": numero, "PtoVta": punto_venta},
        )
        errores = _mensajes(result, "Errors", "Err")
        if any(e.code == NOT_FOUND for e in errores):
            return None
        if errores:
            raise WSFEError(errores)
        r = result.ResultGet
        if r is None or r.Resultado != "A" or not r.CodAutorizacion:
            return None
        doc_tipo = DocTipo(r.DocTipo)
        condicion = r.CondicionIVAReceptorId
        if condicion is None and doc_tipo is DocTipo.CONSUMIDOR_FINAL:
            condicion = CondicionIVAReceptor.CONSUMIDOR_FINAL
        periodo = r.PeriodoAsoc
        return ComprobanteAutorizado(
            cuit=self.cuit,
            punto_venta=int(r.PtoVta),
            tipo=CbteTipo(r.CbteTipo),
            numero=int(r.CbteDesde),
            fecha=_parse_date(r.CbteFch),
            importe_total=_pesos(r.ImpTotal),
            cae=str(r.CodAutorizacion),
            cae_vencimiento=_parse_date(r.FchVto),
            concepto=Concepto(r.Concepto),
            doc_tipo=doc_tipo,
            doc_nro=int(r.DocNro),
            condicion_iva_receptor=CondicionIVAReceptor(condicion) if condicion else None,
            servicio_desde=_parse_date(r.FchServDesde) if r.FchServDesde else None,
            servicio_hasta=_parse_date(r.FchServHasta) if r.FchServHasta else None,
            vencimiento_pago=_parse_date(r.FchVtoPago) if r.FchVtoPago else None,
            observaciones=_mensajes(r, "Observaciones", "Obs"),
            importe_neto=_pesos(r.ImpNeto),
            importe_iva=_pesos(r.ImpIVA),
            importe_no_gravado=_pesos(r.ImpTotConc),
            importe_exento=_pesos(r.ImpOpEx),
            importe_tributos=_pesos(r.ImpTrib),
            iva=[
                IvaAlicuota(_enum(Alicuota, a.Id), _pesos(a.BaseImp), _pesos(a.Importe))
                for a in _lista(r, "Iva", "AlicIva")
            ],
            tributos=[
                Tributo(
                    _enum(TipoTributo, t.Id),
                    _pesos(t.BaseImp),
                    Decimal(str(t.Alic or 0)),
                    _pesos(t.Importe),
                    str(t.Desc or ""),
                )
                for t in _lista(r, "Tributos", "Tributo")
            ],
            asociados=[
                ComprobanteAsociado(
                    tipo=_enum(CbteTipo, c.Tipo),
                    punto_venta=int(c.PtoVta),
                    numero=int(c.Nro),
                    cuit=int(c.Cuit) if c.Cuit else None,
                    fecha=_parse_date(c.CbteFch) if c.CbteFch else None,
                )
                for c in _lista(r, "CbtesAsoc", "CbteAsoc")
            ],
            periodo_asociado=(
                (_parse_date(periodo.FchDesde), _parse_date(periodo.FchHasta))
                if periodo is not None and periodo.FchDesde and periodo.FchHasta
                else None
            ),
        )

    def condiciones_iva_receptor(self, clase: str | None = None) -> dict[int, str]:
        """Condiciones frente al IVA del receptor válidas para la clase (``"A"``, ``"B"``,
        ``"C"``; todas si se omite). Devuelve ``{id: descripción}``."""
        result = self._service().FEParamGetCondicionIvaReceptor(Auth=self._auth(), ClaseCmp=clase)
        return _parametros(result, "CondicionIvaReceptor")

    def tipos_iva(self) -> dict[int, str]:
        """Alícuotas de IVA vigentes (``FEParamGetTiposIva``): ``{id: descripción}``."""
        return _parametros(self._service().FEParamGetTiposIva(Auth=self._auth()), "IvaTipo")

    def tipos_tributo(self) -> dict[int, str]:
        """Tipos de tributo vigentes (``FEParamGetTiposTributos``): ``{id: descripción}``."""
        result = self._service().FEParamGetTiposTributos(Auth=self._auth())
        return _parametros(result, "TributoTipo")

    # --- Emisión -------------------------------------------------------------
    #
    # Todos los create_* calculan el número como el último autorizado + 1. Si otro
    # proceso usa ese número antes (rechazo 10016 y el último autorizado avanzó), se
    # reintenta con el siguiente hasta ``numbering_attempts`` veces: un rechazo no
    # consume número. Ante un corte se verifica con FECompConsultar (ver _recover).

    def create_invoice_a(
        self,
        punto_venta: int,
        *,
        doc_nro: int,
        condicion_iva_receptor: CondicionIVAReceptor,
        neto: Mapping[Alicuota, Decimal | int | str] | None = None,
        no_gravado: Decimal | int | str = 0,
        exento: Decimal | int | str = 0,
        tributos: Iterable[Tributo] = (),
        concepto: Concepto = Concepto.PRODUCTOS,
        fecha: date | None = None,
        servicio_desde: date | None = None,
        servicio_hasta: date | None = None,
        vencimiento_pago: date | None = None,
    ) -> ComprobanteAutorizado:
        """Autoriza una Factura A (responsable inscripto a RI o monotributista).

        - ``neto``: neto gravado por alícuota, ej. ``{Alicuota.IVA_21: "1000.00"}``. El IVA
          se calcula sobre la base de cada alícuota con redondeo half even.
        - ``no_gravado`` y ``exento``: importes no gravados y exentos (sin IVA).
        - ``tributos``: percepciones y otros tributos, que se suman al total.
        - El receptor se identifica siempre con CUIT (``doc_nro``) y su condición frente
          al IVA tiene que ser válida para la clase A (``CONDICIONES_A``).
        - Servicios (concepto 2 o 3): ``servicio_desde`` y ``servicio_hasta`` son
          obligatorios; ``vencimiento_pago`` por defecto es la fecha de emisión.
        """
        return self._create_a(
            CbteTipo.FACTURA_A,
            punto_venta,
            doc_nro=doc_nro,
            condicion_iva_receptor=condicion_iva_receptor,
            neto=neto,
            no_gravado=no_gravado,
            exento=exento,
            tributos=tributos,
            concepto=concepto,
            fecha=fecha,
            servicio_desde=servicio_desde,
            servicio_hasta=servicio_hasta,
            vencimiento_pago=vencimiento_pago,
        )

    def create_credit_note_a(
        self,
        punto_venta: int,
        *,
        asociado: ComprobanteAsociado | ComprobanteAutorizado | None = None,
        periodo: tuple[date, date] | None = None,
        **kwargs,
    ) -> ComprobanteAutorizado:
        """Autoriza una Nota de Crédito A. Recibe los mismos argumentos que
        :meth:`create_invoice_a` más el comprobante A que corrige (``asociado``) o, si
        abarca varios, el ``periodo`` (desde, hasta): uno de los dos."""
        return self._create_a(
            CbteTipo.NOTA_CREDITO_A, punto_venta, asociado=asociado, periodo=periodo, **kwargs
        )

    def create_debit_note_a(
        self,
        punto_venta: int,
        *,
        asociado: ComprobanteAsociado | ComprobanteAutorizado | None = None,
        periodo: tuple[date, date] | None = None,
        **kwargs,
    ) -> ComprobanteAutorizado:
        """Autoriza una Nota de Débito A. Mismos argumentos que :meth:`create_credit_note_a`."""
        return self._create_a(
            CbteTipo.NOTA_DEBITO_A, punto_venta, asociado=asociado, periodo=periodo, **kwargs
        )

    def create_invoice_b(
        self,
        punto_venta: int,
        *,
        neto: Mapping[Alicuota, Decimal | int | str] | None = None,
        no_gravado: Decimal | int | str = 0,
        exento: Decimal | int | str = 0,
        tributos: Iterable[Tributo] = (),
        doc_tipo: DocTipo = DocTipo.CONSUMIDOR_FINAL,
        doc_nro: int = 0,
        condicion_iva_receptor: CondicionIVAReceptor | None = None,
        concepto: Concepto = Concepto.PRODUCTOS,
        fecha: date | None = None,
        servicio_desde: date | None = None,
        servicio_hasta: date | None = None,
        vencimiento_pago: date | None = None,
    ) -> ComprobanteAutorizado:
        """Autoriza una Factura B (responsable inscripto a consumidor final, exento, etc.).

        Los importes se informan igual que en :meth:`create_invoice_a` (neto por
        alícuota; ARCA recibe el IVA discriminado aunque el PDF lo muestre incluido).
        El receptor sigue las reglas de :meth:`create_invoice_c`: consumidor final sin
        identificar por defecto, o identificado con su condición frente al IVA, que
        tiene que ser válida para la clase B (``CONDICIONES_B``).
        """
        importes = _importes(neto, no_gravado, exento, tributos)
        condicion = _condicion_iva(doc_tipo, doc_nro, condicion_iva_receptor)
        _validar_clase(CbteTipo.FACTURA_B, condicion)
        return self._create(
            CbteTipo.FACTURA_B,
            punto_venta,
            importes,
            concepto=concepto,
            doc_tipo=doc_tipo,
            doc_nro=doc_nro,
            condicion=condicion,
            fecha=fecha,
            servicio_desde=servicio_desde,
            servicio_hasta=servicio_hasta,
            vencimiento_pago=vencimiento_pago,
        )

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
        tributos: Iterable[Tributo] = (),
    ) -> ComprobanteAutorizado:
        """Autoriza una Factura C (monotributistas) y devuelve el CAE.

        En clase C no se discrimina IVA: el neto es el importe total menos los
        ``tributos`` (percepciones), si los hay.

        - Consumidor final sin identificar: ``doc_tipo=DocTipo.CONSUMIDOR_FINAL``
          (por defecto), ``doc_nro=0``.
        - Receptor identificado: ``doc_tipo`` CUIT/CUIL/DNI, su número y la
          ``condicion_iva_receptor`` (obligatoria en ese caso).
        - Servicios (concepto 2 o 3): ``servicio_desde`` y ``servicio_hasta``
          son obligatorios; ``vencimiento_pago`` por defecto es la fecha de emisión.
        """
        importes = _importes_c(_amount(importe_total), tributos)
        condicion = _condicion_iva(doc_tipo, doc_nro, condicion_iva_receptor)
        return self._create(
            CbteTipo.FACTURA_C,
            punto_venta,
            importes,
            concepto=concepto,
            doc_tipo=doc_tipo,
            doc_nro=doc_nro,
            condicion=condicion,
            fecha=fecha,
            servicio_desde=servicio_desde,
            servicio_hasta=servicio_hasta,
            vencimiento_pago=vencimiento_pago,
        )

    def _create_a(
        self,
        tipo: CbteTipo,
        punto_venta: int,
        *,
        doc_nro: int,
        condicion_iva_receptor: CondicionIVAReceptor,
        neto: Mapping[Alicuota, Decimal | int | str] | None = None,
        no_gravado: Decimal | int | str = 0,
        exento: Decimal | int | str = 0,
        tributos: Iterable[Tributo] = (),
        concepto: Concepto = Concepto.PRODUCTOS,
        fecha: date | None = None,
        servicio_desde: date | None = None,
        servicio_hasta: date | None = None,
        vencimiento_pago: date | None = None,
        asociado: ComprobanteAsociado | ComprobanteAutorizado | None = None,
        periodo: tuple[date, date] | None = None,
    ) -> ComprobanteAutorizado:
        importes = _importes(neto, no_gravado, exento, tributos)
        condicion = _condicion_iva(DocTipo.CUIT, doc_nro, condicion_iva_receptor)
        _validar_clase(tipo, condicion)
        asociados: list[ComprobanteAsociado] = []
        if tipo is not CbteTipo.FACTURA_A:
            asociados, periodo = _asociacion(asociado, periodo)
        return self._create(
            tipo,
            punto_venta,
            importes,
            concepto=concepto,
            doc_tipo=DocTipo.CUIT,
            doc_nro=doc_nro,
            condicion=condicion,
            fecha=fecha,
            servicio_desde=servicio_desde,
            servicio_hasta=servicio_hasta,
            vencimiento_pago=vencimiento_pago,
            asociados=asociados,
            periodo=periodo,
        )

    def _create(
        self,
        tipo: CbteTipo,
        punto_venta: int,
        importes: "_Importes",
        *,
        concepto: Concepto,
        doc_tipo: DocTipo,
        doc_nro: int,
        condicion: CondicionIVAReceptor,
        fecha: date | None,
        servicio_desde: date | None,
        servicio_hasta: date | None,
        vencimiento_pago: date | None,
        asociados: Iterable[ComprobanteAsociado] = (),
        periodo: tuple[date, date] | None = None,
    ) -> ComprobanteAutorizado:
        """Arma el detalle del comprobante (validando todo antes de llamar a ARCA) y lo emite."""
        fecha = fecha or datetime.now(ARGENTINA_TZ).date()
        concepto = Concepto(concepto)
        servicio = _fechas_servicio(
            concepto, fecha, servicio_desde, servicio_hasta, vencimiento_pago
        )

        detalle = {
            "Concepto": int(concepto),
            "DocTipo": int(doc_tipo),
            "DocNro": int(doc_nro),
            "CbteFch": _fmt(fecha),
            **importes.request(),
            "MonId": "PES",
            "MonCotiz": 1,
            "CondicionIVAReceptorId": int(condicion),
        }
        if servicio["servicio_desde"]:
            detalle |= {
                "FchServDesde": _fmt(servicio["servicio_desde"]),
                "FchServHasta": _fmt(servicio["servicio_hasta"]),
                "FchVtoPago": _fmt(servicio["vencimiento_pago"]),
            }
        if asociados:
            detalle["CbtesAsoc"] = {"CbteAsoc": [_cbte_asoc(a) for a in asociados]}
        if periodo:
            detalle["PeriodoAsoc"] = {"FchDesde": _fmt(periodo[0]), "FchHasta": _fmt(periodo[1])}

        datos = dict(
            cuit=self.cuit,
            punto_venta=punto_venta,
            tipo=tipo,
            concepto=concepto,
            doc_tipo=DocTipo(doc_tipo),
            doc_nro=int(doc_nro),
            condicion_iva_receptor=condicion,
            **servicio,
            **importes.datos(),
            asociados=list(asociados),
            periodo_asociado=periodo,
        )
        return self._emitir(tipo, punto_venta, fecha, detalle, datos)

    def _emitir(
        self, tipo: CbteTipo, punto_venta: int, fecha: date, detalle: dict, datos: dict
    ) -> ComprobanteAutorizado:
        """Numeración y FECAESolicitar, con reintento por número y recuperación ante cortes."""
        ultimo = self.last_voucher_number(punto_venta, tipo)
        for intento in range(1, self.numbering_attempts + 1):
            numero = ultimo + 1
            # Todo lo que puede fallar sin haber enviado nada (ticket, WSDL) va antes del try.
            auth = self._auth()
            service = self._service()
            request = {
                "FeCabReq": {"CantReg": 1, "PtoVta": punto_venta, "CbteTipo": int(tipo)},
                "FeDetReq": {
                    "FECAEDetRequest": [detalle | {"CbteDesde": numero, "CbteHasta": numero}]
                },
            }
            try:
                result = service.FECAESolicitar(Auth=auth, FeCAEReq=request)
                return _parse_cae_response(result, **datos)
            except WSFEError as error:
                # Solo un rechazo explícito por número admite reintento: no se emitió nada.
                if NEXT_NUMBER_MISMATCH not in error.codes or intento == self.numbering_attempts:
                    raise
                ultimo = self.last_voucher_number(punto_venta, tipo)
                if ultimo < numero:  # nadie usó el número: el rechazo es por otra causa (la fecha)
                    raise
            except (*_COMMUNICATION_ERRORS, _UnrecognizedResponse) as error:
                return self._recover(punto_venta, tipo, numero, fecha, datos, error)

    def _recover(
        self, punto_venta: int, tipo: CbteTipo, numero: int, fecha: date, datos: dict, error
    ) -> ComprobanteAutorizado:
        """Tras un corte, verifica con FECompConsultar si ARCA autorizó el comprobante.

        Ver "Operatoria con errores de comunicación" en el manual de WSFEv1.
        """
        ref = dict(punto_venta=punto_venta, tipo=tipo, numero=numero)
        nombre = f"{punto_venta:05d}-{numero:08d}"
        last_error = None
        for intento in range(self.recovery_attempts):
            if intento:
                time.sleep(self.recovery_delay)
            try:
                existente = self.get_voucher(punto_venta, tipo, numero)
                break
            except Exception as e:  # noqa: BLE001 - cualquier fallo deja el resultado incierto
                last_error = e
        else:
            raise WSFEConnectionError(
                f"Se cortó la comunicación al pedir el CAE del comprobante {nombre} y no se pudo "
                f"verificar si ARCA lo autorizó ({last_error}). NO reintentes la emisión: "
                f"consultá get_voucher({punto_venta}, {tipo!r}, {numero}).",
                **ref,
                safe_to_retry=False,
            ) from error

        if existente is None:
            raise WSFEConnectionError(
                f"Se cortó la comunicación al pedir el CAE del comprobante {nombre}. ARCA no lo "
                f"registra, así que es seguro reintentar. Error original: {error}",
                **ref,
                safe_to_retry=True,
            ) from error

        if (
            existente.importe_total != datos["importe_total"]
            or existente.importe_neto != datos["importe_neto"]
            or existente.importe_iva != datos["importe_iva"]
            or existente.doc_tipo != datos["doc_tipo"]
            or existente.doc_nro != datos["doc_nro"]
            or existente.fecha != fecha
        ):
            raise WSFEConnectionError(
                f"Se cortó la comunicación y el comprobante {nombre} figura autorizado, pero con "
                f"datos distintos a los enviados (¿otro proceso usó ese número?). Revisalo "
                f"antes de reintentar.",
                **ref,
                safe_to_retry=False,
            ) from error

        # Autorizado: devolvemos los datos enviados más el CAE registrado en ARCA.
        return ComprobanteAutorizado(
            numero=numero,
            fecha=existente.fecha,
            cae=existente.cae,
            cae_vencimiento=existente.cae_vencimiento,
            observaciones=existente.observaciones,
            **datos,
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


# --- Importes ------------------------------------------------------------------


@dataclass(frozen=True)
class _Importes:
    """Composición del total: neto gravado + no gravado + exento + IVA + tributos."""

    neto: Decimal
    no_gravado: Decimal
    exento: Decimal
    iva: list[IvaAlicuota]
    tributos: list[Tributo]

    @property
    def importe_iva(self) -> Decimal:
        return sum((a.importe for a in self.iva), Decimal("0.00"))

    @property
    def importe_tributos(self) -> Decimal:
        return sum((t.importe for t in self.tributos), Decimal("0.00"))

    @property
    def total(self) -> Decimal:
        return self.neto + self.no_gravado + self.exento + self.importe_iva + self.importe_tributos

    def request(self) -> dict:
        detalle = {
            "ImpTotal": float(self.total),
            "ImpTotConc": float(self.no_gravado),
            "ImpNeto": float(self.neto),
            "ImpOpEx": float(self.exento),
            "ImpTrib": float(self.importe_tributos),
            "ImpIVA": float(self.importe_iva),
        }
        if self.iva:
            detalle["Iva"] = {
                "AlicIva": [
                    {
                        "Id": int(a.alicuota),
                        "BaseImp": float(a.base_imponible),
                        "Importe": float(a.importe),
                    }
                    for a in self.iva
                ]
            }
        if self.tributos:
            detalle["Tributos"] = {
                "Tributo": [
                    {
                        "Id": int(t.tipo),
                        "Desc": t.descripcion,
                        "BaseImp": float(t.base_imponible),
                        "Alic": float(t.alicuota),
                        "Importe": float(t.importe),
                    }
                    for t in self.tributos
                ]
            }
        return detalle

    def datos(self) -> dict:
        """Campos de ``ComprobanteAutorizado``."""
        return dict(
            importe_total=self.total,
            importe_neto=self.neto,
            importe_iva=self.importe_iva,
            importe_no_gravado=self.no_gravado,
            importe_exento=self.exento,
            importe_tributos=self.importe_tributos,
            iva=list(self.iva),
            tributos=list(self.tributos),
        )


def _importes(
    neto: Mapping[Alicuota, Decimal | int | str] | None,
    no_gravado: Decimal | int | str,
    exento: Decimal | int | str,
    tributos: Iterable[Tributo],
) -> _Importes:
    """Importes de un comprobante A o B: el IVA se calcula sobre la base de cada alícuota."""
    iva = []
    for alicuota, valor in sorted((Alicuota(a), v) for a, v in (neto or {}).items()):
        base = _monto(valor, f"El neto al {alicuota.porcentaje}%")
        if base:
            importe = (base * alicuota.porcentaje / 100).quantize(_CENTS, rounding=ROUND_HALF_EVEN)
            iva.append(IvaAlicuota(alicuota, base, importe))
    importes = _Importes(
        neto=sum((a.base_imponible for a in iva), Decimal("0.00")),
        no_gravado=_monto(no_gravado, "El importe no gravado"),
        exento=_monto(exento, "El importe exento"),
        iva=iva,
        tributos=_tributos(tributos),
    )
    if importes.total <= 0:
        raise ValueError("El importe total debe ser mayor a 0: indicá el neto por alícuota")
    return importes


def _importes_c(total: Decimal, tributos: Iterable[Tributo]) -> _Importes:
    tributos = _tributos(tributos)
    neto = total - sum((t.importe for t in tributos), Decimal("0.00"))
    if neto <= 0:
        raise ValueError("Los tributos no pueden igualar ni superar el importe total")
    return _Importes(
        neto=neto, no_gravado=Decimal("0.00"), exento=Decimal("0.00"), iva=[], tributos=tributos
    )


def _tributos(tributos: Iterable[Tributo]) -> list[Tributo]:
    tributos = list(tributos)
    if not all(isinstance(t, Tributo) for t in tributos):
        raise TypeError("Los tributos se indican con objetos Tributo")
    return tributos


def _fechas_servicio(
    concepto: Concepto,
    fecha: date,
    desde: date | None,
    hasta: date | None,
    vencimiento_pago: date | None,
) -> dict:
    """Período y vencimiento del pago: obligatorios para servicios, ignorados para productos."""
    if concepto is Concepto.PRODUCTOS:
        return dict(servicio_desde=None, servicio_hasta=None, vencimiento_pago=None)
    if not (desde and hasta):
        raise ValueError("Para servicios hay que indicar servicio_desde y servicio_hasta")
    if hasta < desde:
        raise ValueError("servicio_hasta no puede ser anterior a servicio_desde")
    vencimiento_pago = vencimiento_pago or fecha
    if vencimiento_pago < fecha:
        raise ValueError("vencimiento_pago no puede ser anterior a la fecha de emisión")
    return dict(servicio_desde=desde, servicio_hasta=hasta, vencimiento_pago=vencimiento_pago)


# --- Receptor y comprobantes asociados -------------------------------------------


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
    if doc_tipo in (DocTipo.CUIT, DocTipo.CUIL) and not is_valid_cuit(str(doc_nro)):
        raise ValueError(f"{doc_tipo.name} del receptor inválido: {doc_nro}")
    if condicion is None:
        raise ValueError("Con un receptor identificado hay que indicar condicion_iva_receptor")
    return CondicionIVAReceptor(condicion)


def _validar_clase(tipo: CbteTipo, condicion: CondicionIVAReceptor) -> None:
    if tipo.letra == "A" and condicion not in CONDICIONES_A:
        raise ValueError(
            f"Un comprobante A no se puede emitir a un receptor {condicion.name}: a "
            f"consumidores finales, exentos y no categorizados les corresponde la Factura B"
        )
    if tipo.letra == "B" and condicion not in CONDICIONES_B:
        raise ValueError(
            f"Un comprobante B no se puede emitir a un receptor {condicion.name}: a "
            f"responsables inscriptos y monotributistas les corresponde la Factura A"
        )


def _asociacion(
    asociado: ComprobanteAsociado | ComprobanteAutorizado | None,
    periodo: tuple[date, date] | None,
) -> tuple[list[ComprobanteAsociado], tuple[date, date] | None]:
    """Valida el comprobante o período al que se refiere una nota de crédito/débito."""
    if (asociado is None) == (periodo is None):
        raise ValueError(
            "Una nota de crédito/débito se refiere a un comprobante (asociado=) o a un "
            "período (periodo=): indicá uno de los dos"
        )
    if periodo is not None:
        desde, hasta = periodo
        if hasta < desde:
            raise ValueError("El período asociado termina antes de empezar")
        return [], (desde, hasta)
    if isinstance(asociado, ComprobanteAutorizado):
        asociado = ComprobanteAsociado.from_comprobante(asociado)
    if asociado.tipo not in set(CbteTipo) or CbteTipo(asociado.tipo).letra != "A":
        raise ValueError(f"Una nota A solo puede referirse a un comprobante A, no {asociado.tipo}")
    return [asociado], None


def _cbte_asoc(asociado: ComprobanteAsociado) -> dict:
    cbte = {"Tipo": int(asociado.tipo), "PtoVta": asociado.punto_venta, "Nro": asociado.numero}
    if asociado.cuit:
        cbte["Cuit"] = str(asociado.cuit)
    if asociado.fecha:
        cbte["CbteFch"] = _fmt(asociado.fecha)
    return cbte


# --- Respuestas ----------------------------------------------------------------


def _parse_cae_response(result, **datos) -> ComprobanteAutorizado:
    errores = _mensajes(result, "Errors", "Err")
    eventos = _mensajes(result, "Events", "Evt")
    detalles = getattr(getattr(result, "FeDetResp", None), "FECAEDetResponse", None) or []
    det = detalles[0] if detalles else None
    observaciones = _mensajes(det, "Observaciones", "Obs") if det is not None else []

    if det is None:
        if errores:
            raise WSFEError(errores)
        # Nunca reportar como rechazo algo que no pudimos interpretar: puede estar autorizado.
        raise _UnrecognizedResponse(
            "Respuesta de FECAESolicitar sin detalle reconocible; el comprobante podría "
            f"haber sido autorizado. Respuesta: {result!r}"
        )
    if det.Resultado != "A" or not det.CAE:
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


def _parametros(result, item: str) -> dict[int, str]:
    errores = _mensajes(result, "Errors", "Err")
    if errores:
        raise WSFEError(errores)
    return {int(p.Id): str(p.Desc) for p in _lista(result, "ResultGet", item)}


def _lista(obj, container: str, item: str) -> list:
    return getattr(getattr(obj, container, None), item, None) or []


def _mensajes(obj, container: str, item: str) -> list[Mensaje]:
    return [Mensaje(int(m.Code), str(m.Msg)) for m in _lista(obj, container, item)]


def _enum(cls, value):
    """Miembro del enum, o el entero tal cual si ARCA devuelve un código que no conocemos."""
    try:
        return cls(int(value))
    except ValueError:
        return int(value)


def _amount(value: Decimal | int | str) -> Decimal:
    amount = _monto(value, "El importe total")
    if amount <= 0:
        raise ValueError("El importe total debe ser mayor a 0")
    return amount


def _pesos(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(_CENTS)


def _fmt(d: date) -> str:
    return d.strftime("%Y%m%d")


def _parse_date(value: str) -> date:
    return datetime.strptime(str(value), "%Y%m%d").date()
