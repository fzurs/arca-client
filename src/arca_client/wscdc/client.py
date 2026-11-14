"""Cliente de WSCDC (Constatación de Comprobantes).

Verifica contra ARCA que un comprobante recibido (por ejemplo, la factura de un
proveedor) exista y esté autorizado con los datos que figuran en él.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum

import zeep

from ..certificates import cuit_from_certificate, is_valid_cuit
from ..environments import wscdc_wsdl
from ..exceptions import ArcaError, RespuestaNoReconocida, WSCDCError
from ..transport import make_transport
from ..wsaa import WSAA
from ..wsfe.client import _lista, _mensajes
from ..wsfe.qr import parse_qr_url
from ..wsfe.types import CbteTipo, DocTipo, Mensaje, _monto

SERVICE = "wscdc"


class ModoAutorizacion(str, Enum):
    """Cómo se autorizó el comprobante (``ComprobantesModalidadConsultar``)."""

    CAE = "CAE"
    CAEA = "CAEA"
    CAI = "CAI"


# "tipoCodAut" del QR (RG 4892).
_MODO_QR = {"E": ModoAutorizacion.CAE, "A": ModoAutorizacion.CAEA}


@dataclass(frozen=True)
class ComprobanteConsultado:
    """Datos con los que se consultó a ARCA, para compararlos con el comprobante recibido."""

    cuit_emisor: int
    punto_venta: int
    tipo: int
    numero: int
    fecha: date
    importe_total: Decimal
    codigo_autorizacion: str
    modo: ModoAutorizacion
    doc_tipo_receptor: int | None = None
    doc_nro_receptor: str | None = None


@dataclass(frozen=True)
class Constatacion:
    """Resultado de ``ComprobanteConstatar``.

    ``aprobada`` es ``True`` solo si ARCA registra un comprobante autorizado con esos
    datos. Si no, ``observaciones`` y ``errores`` dicen por qué.

    ARCA tolera una diferencia de alrededor de $1 en el importe (en homologación
    aprobó ±1,01 y rechazó ±1,50) y no devuelve el importe registrado: para un control
    exacto, compará además el importe con tus registros.

    ``comprobante`` tiene los datos que se enviaron a ARCA (con ``verify_qr``, los del
    QR): compará que coincidan con la factura impresa o con tus registros.
    """

    aprobada: bool
    observaciones: list[Mensaje] = field(default_factory=list)
    errores: list[Mensaje] = field(default_factory=list)
    eventos: list[Mensaje] = field(default_factory=list)
    fecha_proceso: str | None = None
    comprobante: ComprobanteConsultado | None = None


class WSCDC:
    """Constata comprobantes emitidos por terceros.

    >>> wsaa = WSAA("cert.crt", "private.key", Environment.HOMOLOGACION)
    >>> wscdc = WSCDC(wsaa)
    >>> wscdc.verify_qr("https://www.arca.gob.ar/fe/qr/?p=eyJ2ZXIiOjEs...").aprobada
    True
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

    def verify_voucher(
        self,
        *,
        cuit_emisor: int | str,
        punto_venta: int,
        tipo: CbteTipo | int,
        numero: int,
        fecha: date,
        importe_total,
        codigo_autorizacion: str | int,
        doc_tipo_receptor: DocTipo | int | None = None,
        doc_nro_receptor: int | str | None = None,
        modo: ModoAutorizacion = ModoAutorizacion.CAE,
    ) -> Constatacion:
        """Verifica un comprobante con los datos impresos en él.

        - ``tipo`` admite cualquier código de ARCA (ej. 201 para FCE), no solo los de
          :class:`CbteTipo`.
        - ``importe_total`` en la moneda del comprobante (``Decimal``, ``int`` o ``str``).
        - ``codigo_autorizacion``: el CAE, CAEA o CAI según ``modo``.
        - El documento del receptor es opcional, pero si se indica van los dos datos.
          Para las facturas que te emiten a vos: ``DocTipo.CUIT`` y tu CUIT.
        """
        cuit_emisor = str(cuit_emisor).replace("-", "")
        if not is_valid_cuit(cuit_emisor):
            raise ValueError(f"CUIT del emisor inválida: {cuit_emisor}")
        if punto_venta <= 0 or numero <= 0:
            raise ValueError("El punto de venta y el número tienen que ser mayores a 0")
        codigo = str(codigo_autorizacion).strip()
        if not codigo.isdigit():
            raise ValueError(f"Código de autorización inválido: {codigo_autorizacion!r}")
        if (doc_tipo_receptor is None) != (doc_nro_receptor is None):
            raise ValueError("Indicá doc_tipo_receptor y doc_nro_receptor juntos, o ninguno")

        importe = _monto(importe_total, "El importe total")
        receptor = (
            (int(doc_tipo_receptor), str(doc_nro_receptor).replace("-", "").strip())
            if doc_tipo_receptor is not None
            else (None, None)
        )
        consultado = ComprobanteConsultado(
            cuit_emisor=int(cuit_emisor),
            punto_venta=int(punto_venta),
            tipo=int(tipo),
            numero=int(numero),
            fecha=fecha,
            importe_total=importe,
            codigo_autorizacion=codigo,
            modo=ModoAutorizacion(modo),
            doc_tipo_receptor=receptor[0],
            doc_nro_receptor=receptor[1],
        )
        request = {
            "CbteModo": consultado.modo.value,
            "CuitEmisor": consultado.cuit_emisor,
            "PtoVta": consultado.punto_venta,
            "CbteTipo": consultado.tipo,
            "CbteNro": consultado.numero,
            "CbteFch": fecha.strftime("%Y%m%d"),
            "ImpTotal": float(importe),
            "CodAutorizacion": codigo,
        }
        if consultado.doc_tipo_receptor is not None:
            request["DocTipoReceptor"] = str(consultado.doc_tipo_receptor)
            request["DocNroReceptor"] = consultado.doc_nro_receptor

        result = self._service().ComprobanteConstatar(Auth=self._auth(), CmpReq=request)
        errores = _mensajes(result, "Errors", "Err")
        resultado = getattr(result, "Resultado", None)
        if resultado not in ("A", "R"):
            if errores:
                raise WSCDCError(errores)
            # Nunca informar como inválido algo que no pudimos interpretar.
            raise RespuestaNoReconocida(
                "Respuesta de ComprobanteConstatar sin resultado (la respuesta completa está "
                "en el atributo `respuesta`)",
                respuesta=result,
            )
        return Constatacion(
            aprobada=resultado == "A" and not errores,
            observaciones=_mensajes(result, "Observaciones", "Obs"),
            errores=errores,
            # ARCA suele mandar un evento vacío (Code 0, sin Msg).
            eventos=[
                Mensaje(int(e.Code), str(e.Msg))
                for e in _lista(result, "Events", "Evt")
                if e.Code or e.Msg
            ],
            fecha_proceso=str(result.FchProceso) if result.FchProceso else None,
            comprobante=consultado,
        )

    def verify_qr(
        self,
        url: str,
        *,
        doc_tipo_receptor: DocTipo | int | None = None,
        doc_nro_receptor: int | str | None = None,
    ) -> Constatacion:
        """Verifica un comprobante a partir del texto de su código QR (RG 4892).

        Documento del receptor:

        - Si lo indicás (``doc_tipo_receptor`` y ``doc_nro_receptor``), se consulta con ese.
          Así se exige que el comprobante esté emitido a tu nombre: si el QR trae otro
          receptor, se lanza ``ValueError`` sin consultar a ARCA.
        - Si no lo indicás, se usa el del QR; si el QR tampoco lo trae (consumidor
          final), se consulta sin receptor.

        Un QR mal formado o incompleto lanza ``ValueError`` (ver :func:`parse_qr_url`).
        """
        datos = parse_qr_url(url)
        try:
            modo = _MODO_QR[str(datos.get("tipoCodAut", "E"))]
            qr_tiene_receptor = "tipoDocRec" in datos and "nroDocRec" in datos
            if doc_tipo_receptor is None and doc_nro_receptor is None:
                if qr_tiene_receptor:
                    doc_tipo_receptor = datos["tipoDocRec"]
                    doc_nro_receptor = datos["nroDocRec"]
            elif (
                qr_tiene_receptor
                and doc_tipo_receptor is not None
                and doc_nro_receptor is not None
                and _clave_receptor(doc_tipo_receptor, doc_nro_receptor)
                != _clave_receptor(datos["tipoDocRec"], datos["nroDocRec"])
            ):
                raise ValueError(
                    "El comprobante no está emitido a ese documento: el QR indica otro "
                    "receptor. No se consultó a ARCA."
                )
            return self.verify_voucher(
                cuit_emisor=datos["cuit"],
                punto_venta=int(datos["ptoVta"]),
                tipo=int(datos["tipoCmp"]),
                numero=int(datos["nroCmp"]),
                fecha=date.fromisoformat(str(datos["fecha"])),
                importe_total=str(datos["importe"]),
                codigo_autorizacion=datos["codAut"],
                doc_tipo_receptor=doc_tipo_receptor,
                doc_nro_receptor=doc_nro_receptor,
                modo=modo,
            )
        except KeyError as e:
            raise ValueError(f"Al QR le falta el dato {e}") from e

    # --- Consultas de parámetros ----------------------------------------------

    def modalidades(self) -> dict[str, str]:
        """Modalidades de autorización (``CAE``, ``CAEA``, ``CAI``): ``{código: descripción}``."""
        result = self._service().ComprobantesModalidadConsultar(Auth=self._auth())
        return _parametros(result, "FacModTipo", "Cod")

    def tipos_comprobante(self) -> dict[int, str]:
        """Tipos de comprobante que se pueden constatar: ``{id: descripción}``."""
        result = self._service().ComprobantesTipoConsultar(Auth=self._auth())
        return {int(k): v for k, v in _parametros(result, "CbteTipo", "Id").items()}

    def tipos_documento(self) -> dict[str, str]:
        """Tipos de documento del receptor: ``{id: descripción}``."""
        result = self._service().DocumentosTipoConsultar(Auth=self._auth())
        return _parametros(result, "DocTipo", "Id")

    def estado(self) -> dict[str, str]:
        """Estado de los servidores de WSCDC (``ComprobanteDummy``, no requiere ticket)."""
        r = self._service().ComprobanteDummy()
        return {"AppServer": r.AppServer, "DbServer": r.DbServer, "AuthServer": r.AuthServer}

    # --- Infraestructura -------------------------------------------------------

    def _auth(self) -> dict:
        ticket = self.wsaa.get_ticket(SERVICE)
        return {"Token": ticket.token, "Sign": ticket.sign, "Cuit": self.cuit}

    def _service(self):
        if self._soap is None:
            transport = make_transport(self.env, self.timeout)
            self._soap = zeep.Client(wscdc_wsdl(self.env), transport=transport)
        return self._soap.service


# CUIT (80), CUIL (86) y CDI (87) son la misma clave de 11 dígitos: un QR con CUIL y
# quien llama con CUIT del mismo número son la misma persona.
_CLAVES_FISCALES = frozenset({80, 86, 87})


def _clave_receptor(tipo, numero) -> tuple[int | str, str]:
    """Documento del receptor normalizado para comparar: sin guiones ni ceros a la izquierda."""
    nro = str(numero).replace("-", "").strip().upper()
    if nro.isdigit():
        nro = nro.lstrip("0") or "0"
    tipo = int(tipo)
    return ("clave fiscal" if tipo in _CLAVES_FISCALES else tipo), nro


def _parametros(result, item: str, clave: str) -> dict[str, str]:
    errores = _mensajes(result, "Errors", "Err")
    if errores:
        raise WSCDCError(errores)
    return {str(getattr(p, clave)): str(p.Desc) for p in _lista(result, "ResultGet", item)}
