import base64
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import zeep

from arca_client import (
    WSAA,
    WSCDC,
    ArcaError,
    CbteTipo,
    ComprobanteAutorizado,
    Concepto,
    CondicionIVAReceptor,
    DocTipo,
    MemoryTicketCache,
    WSCDCError,
    parse_qr_url,
    qr_url,
)
from arca_client.wsaa.ticket import AccessTicket

# Tipos del WSDL real de WSCDC: si un nombre de campo no existe, zeep lanza TypeError.
WSDL = Path(__file__).parent / "fixtures" / "wscdc.wsdl"
_ZEEP = zeep.Client(str(WSDL))

CUIT_PROVEEDOR = 30712345671
CAE = "76123456789012"


def _t(name, **kwargs):
    return _ZEEP.get_type(f"ns0:{name}")(**kwargs)


def _array(kind, items):
    if not items:
        return None
    return _t(f"ArrayOf{kind}", **{kind: [_t(kind, Code=c, Msg=m) for c, m in items]})


class FakeService:
    def __init__(self, resultado="A", obs=None, errores=None):
        self.resultado = resultado
        self.obs = obs
        self.errores = errores
        self.requests = []

    def ComprobanteConstatar(self, Auth, CmpReq):
        self.requests.append((Auth, CmpReq))
        return _t(
            "CmpResponse",
            CmpResp=_t("CmpDatos", **CmpReq) if self.resultado else None,
            Resultado=self.resultado,
            Observaciones=_array("Obs", self.obs),
            FchProceso="20261027233000" if self.resultado else None,
            # Así responde homologación: un evento vacío.
            Events=_t("ArrayOfEvt", Evt=[_t("Evt", Code=0, Msg=None)]),
            Errors=_array("Err", self.errores),
        )

    def ComprobantesTipoConsultar(self, Auth):
        tipos = [_t("CbteTipo", Id=1, Desc="Factura A"), _t("CbteTipo", Id=201, Desc="FCE A")]
        return _t(
            "CbteTipoResponse",
            ResultGet=_t("ArrayOfCbteTipo", CbteTipo=tipos),
            Errors=_array("Err", self.errores),
        )

    def ComprobanteDummy(self):
        return _t("DummyResponse", AppServer="OK", DbServer="OK", AuthServer="OK")


@pytest.fixture
def wscdc(cert_pem, key_pem):
    cache = MemoryTicketCache()
    wsaa = WSAA(cert_pem, key_pem, cache=cache)
    now = datetime.now(timezone.utc)
    ticket = AccessTicket("TOKEN", "SIGN", now, now + timedelta(hours=12))
    cache.set(wsaa._cache_key("wscdc"), ticket)
    client = WSCDC(wsaa)
    client.fake = FakeService()
    client._service = lambda: client.fake
    return client


def _verificar(wscdc, **kwargs):
    datos = dict(
        cuit_emisor=CUIT_PROVEEDOR,
        punto_venta=3,
        tipo=CbteTipo.FACTURA_A,
        numero=42,
        fecha=date(2026, 10, 19),
        importe_total="1210.00",
        codigo_autorizacion=CAE,
        doc_tipo_receptor=DocTipo.CUIT,
        doc_nro_receptor=20123456780,
    )
    return wscdc.verify_voucher(**(datos | kwargs))


def test_constatacion_aprobada(wscdc):
    resultado = _verificar(wscdc)
    assert resultado.aprobada
    assert resultado.observaciones == resultado.errores == resultado.eventos == []
    assert resultado.fecha_proceso == "20261027233000"

    auth, req = wscdc.fake.requests[0]
    assert auth == {"Token": "TOKEN", "Sign": "SIGN", "Cuit": 20123456780}
    assert req == {
        "CbteModo": "CAE",
        "CuitEmisor": CUIT_PROVEEDOR,
        "PtoVta": 3,
        "CbteTipo": 1,
        "CbteNro": 42,
        "CbteFch": "20261019",
        "ImpTotal": 1210.0,
        "CodAutorizacion": CAE,
        "DocTipoReceptor": "80",
        "DocNroReceptor": "20123456780",
    }


def test_sin_receptor_y_tipo_no_soportado_para_emitir(wscdc):
    _verificar(wscdc, tipo=201, doc_tipo_receptor=None, doc_nro_receptor=None)
    _, req = wscdc.fake.requests[0]
    assert req["CbteTipo"] == 201
    assert "DocTipoReceptor" not in req and "DocNroReceptor" not in req


def test_rechazo_con_observaciones(wscdc):
    wscdc.fake.resultado = "R"
    wscdc.fake.obs = [(1, "El importe informado no corresponde")]
    resultado = _verificar(wscdc)
    assert not resultado.aprobada
    assert [o.code for o in resultado.observaciones] == [1]


def test_rechazo_con_errores_no_lanza(wscdc):
    wscdc.fake.resultado = "R"
    wscdc.fake.errores = [(100, "Comprobante inexistente")]
    resultado = _verificar(wscdc)
    assert not resultado.aprobada
    assert [e.code for e in resultado.errores] == [100]


def test_aprobado_con_errores_no_se_considera_aprobado(wscdc):
    wscdc.fake.errores = [(500, "Algo raro")]
    assert not _verificar(wscdc).aprobada


def test_errores_sin_resultado_lanzan(wscdc):
    wscdc.fake.resultado = None
    wscdc.fake.errores = [(600, "No autorizado")]
    with pytest.raises(WSCDCError) as e:
        _verificar(wscdc)
    assert e.value.codes == [600]


def test_respuesta_sin_resultado_no_se_informa_como_invalida(wscdc):
    wscdc.fake.resultado = None
    with pytest.raises(ArcaError, match="sin resultado"):
        _verificar(wscdc)


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"cuit_emisor": 30712345670}, ValueError),  # dígito verificador
        ({"importe_total": 1210.0}, TypeError),
        ({"importe_total": "12.345"}, ValueError),
        ({"codigo_autorizacion": "CAE-123"}, ValueError),
        ({"numero": 0}, ValueError),
        ({"doc_nro_receptor": None}, ValueError),
        ({"modo": "XYZ"}, ValueError),
    ],
)
def test_validaciones_locales(wscdc, kwargs, error):
    with pytest.raises(error):
        _verificar(wscdc, **kwargs)
    assert not wscdc.fake.requests


def _comprobante(**overrides):
    datos = dict(
        cuit=CUIT_PROVEEDOR,
        punto_venta=3,
        tipo=CbteTipo.FACTURA_B,
        numero=7,
        fecha=date(2026, 10, 19),
        importe_total=Decimal("1500.50"),
        cae=CAE,
        cae_vencimiento=date(2026, 10, 29),
        concepto=Concepto.PRODUCTOS,
        doc_tipo=DocTipo.CUIT,
        doc_nro=20123456780,
        condicion_iva_receptor=CondicionIVAReceptor.IVA_SUJETO_EXENTO,
    )
    return ComprobanteAutorizado(**(datos | overrides))


def test_verify_qr(wscdc):
    assert wscdc.verify_qr(qr_url(_comprobante())).aprobada
    _, req = wscdc.fake.requests[0]
    assert req == {
        "CbteModo": "CAE",
        "CuitEmisor": CUIT_PROVEEDOR,
        "PtoVta": 3,
        "CbteTipo": 6,
        "CbteNro": 7,
        "CbteFch": "20261019",
        "ImpTotal": 1500.5,
        "CodAutorizacion": CAE,
        "DocTipoReceptor": "80",
        "DocNroReceptor": "20123456780",
    }


def test_verify_qr_consumidor_final_con_documento_aparte(wscdc):
    comprobante = _comprobante(
        doc_tipo=DocTipo.CONSUMIDOR_FINAL,
        doc_nro=0,
        condicion_iva_receptor=CondicionIVAReceptor.CONSUMIDOR_FINAL,
    )
    wscdc.verify_qr(qr_url(comprobante))
    wscdc.verify_qr(qr_url(comprobante), doc_tipo_receptor=DocTipo.DNI, doc_nro_receptor=12345678)
    (_, sin_doc), (_, con_doc) = wscdc.fake.requests
    assert "DocTipoReceptor" not in sin_doc
    assert con_doc["DocTipoReceptor"] == "96" and con_doc["DocNroReceptor"] == "12345678"


def _qr(payload, *, urlsafe=False, sin_relleno=False, dominio="www.arca.gob.ar"):
    raw = json.dumps(payload).encode()
    data = (base64.urlsafe_b64encode if urlsafe else base64.b64encode)(raw).decode()
    if sin_relleno:
        data = data.rstrip("=")
    return f"https://{dominio}/fe/qr/?p={data}"


QR_CAEA = {
    "ver": 1,
    "fecha": "2026-10-19",
    "cuit": CUIT_PROVEEDOR,
    "ptoVta": 5,
    "tipoCmp": 11,
    "nroCmp": 9,
    "importe": 99.9,
    "moneda": "PES",
    "ctz": 1,
    "tipoCodAut": "A",
    "codAut": 31234567890123,
}


def test_verify_qr_caea_y_variantes_de_codificacion(wscdc):
    wscdc.verify_qr(_qr(QR_CAEA, urlsafe=True, sin_relleno=True, dominio="www.afip.gob.ar"))
    _, req = wscdc.fake.requests[0]
    assert req["CbteModo"] == "CAEA"
    assert req["ImpTotal"] == 99.9
    assert req["CodAutorizacion"] == "31234567890123"


def test_parse_qr_url():
    datos = parse_qr_url(qr_url(_comprobante()))
    assert datos["importe"] == Decimal("1500.5") and isinstance(datos["importe"], Decimal)
    assert datos["codAut"] == int(CAE)
    # Un "+" del Base64 sin codificar en la URL llega como espacio.
    url = qr_url(_comprobante())
    assert parse_qr_url(url.replace("+", " ")) == datos


@pytest.mark.parametrize(
    "url",
    [
        "https://www.arca.gob.ar/fe/qr/",
        "https://www.arca.gob.ar/fe/qr/?p=@@@",
        _qr(["no", "es", "un", "objeto"]),
    ],
)
def test_parse_qr_url_invalido(url):
    with pytest.raises(ValueError):
        parse_qr_url(url)


def test_verify_qr_incompleto(wscdc):
    datos = {k: v for k, v in QR_CAEA.items() if k != "codAut"}
    with pytest.raises(ValueError, match="codAut"):
        wscdc.verify_qr(_qr(datos))
    assert not wscdc.fake.requests


def test_parametros_y_estado(wscdc):
    assert wscdc.tipos_comprobante() == {1: "Factura A", 201: "FCE A"}
    assert wscdc.estado() == {"AppServer": "OK", "DbServer": "OK", "AuthServer": "OK"}
    wscdc.fake.errores = [(600, "No autorizado")]
    with pytest.raises(WSCDCError):
        wscdc.tipos_comprobante()
