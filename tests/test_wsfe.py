from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import zeep

from arca_client import (
    WSAA,
    WSFE,
    ArcaError,
    CbteTipo,
    Concepto,
    CondicionIVAReceptor,
    DocTipo,
    MemoryTicketCache,
    WSFEError,
)
from arca_client.wsaa.ticket import AccessTicket

# Tipos del WSDL real de WSFEv1: si un nombre de campo no existe, zeep lanza TypeError.
WSDL = Path(__file__).parent / "fixtures" / "wsfev1.wsdl"
_ZEEP = zeep.Client(str(WSDL))


def _t(name, **kwargs):
    return _ZEEP.get_type(f"ns0:{name}")(**kwargs)


def _array(kind, items):
    if not items:
        return None
    return _t(f"ArrayOf{kind}", **{kind: [_t(kind, Code=c, Msg=m) for c, m in items]})


class FakeService:
    def __init__(self, last=41, resultado="A", errores=None, obs=None):
        self.last = last
        self.resultado = resultado
        self.errores = errores
        self.obs = obs
        self.requests = []

    def FECompUltimoAutorizado(self, Auth, PtoVta, CbteTipo):
        self.requests.append(("ultimo", Auth, PtoVta, CbteTipo))
        return _t("FERecuperaLastCbteResponse", PtoVta=PtoVta, CbteTipo=CbteTipo, CbteNro=self.last)

    def FECAESolicitar(self, Auth, FeCAEReq):
        self.requests.append(("cae", Auth, FeCAEReq))
        det = FeCAEReq["FeDetReq"]["FECAEDetRequest"][0]
        aprobado = self.resultado == "A"
        detalle = _t(
            "FECAEDetResponse",
            Concepto=det["Concepto"],
            DocTipo=det["DocTipo"],
            DocNro=det["DocNro"],
            CbteDesde=det["CbteDesde"],
            CbteHasta=det["CbteHasta"],
            CbteFch=det["CbteFch"],
            Resultado=self.resultado,
            Observaciones=_array("Obs", self.obs),
            CAE="76123456789012" if aprobado else "",
            CAEFchVto="20261029" if aprobado else "",
        )
        return _t(
            "FECAEResponse",
            FeCabResp=_t(
                "FECAECabResponse",
                Cuit=Auth["Cuit"],
                PtoVta=FeCAEReq["FeCabReq"]["PtoVta"],
                CbteTipo=FeCAEReq["FeCabReq"]["CbteTipo"],
                FchProceso="20261019201144",
                CantReg=1,
                Resultado=self.resultado,
                Reproceso="N",
            ),
            FeDetResp=_t("ArrayOfFECAEDetResponse", FECAEDetResponse=[detalle]),
            Errors=_array("Err", self.errores),
        )


@pytest.fixture
def wsfe(cert_pem, key_pem):
    cache = MemoryTicketCache()
    wsaa = WSAA(cert_pem, key_pem, cache=cache)
    now = datetime.now(timezone.utc)
    ticket = AccessTicket("TOKEN", "SIGN", now, now + timedelta(hours=12))
    cache.set(wsaa._cache_key("wsfe"), ticket)
    client = WSFE(wsaa)
    client.fake = FakeService()
    client._service = lambda: client.fake
    return client


def _detalle(wsfe):
    (_, _, req) = wsfe.fake.requests[-1]
    return req["FeCabReq"], req["FeDetReq"]["FECAEDetRequest"][0]


def test_cuit_from_certificate(wsfe):
    assert wsfe.cuit == 20123456780


def test_factura_c_consumidor_final(wsfe):
    factura = wsfe.create_invoice_c(1, "1500.50", fecha=date(2026, 10, 19))

    assert factura.numero == 42
    assert factura.cae == "76123456789012"
    assert factura.cae_vencimiento == date(2026, 10, 29)
    assert factura.importe_total == Decimal("1500.50")

    cab, det = _detalle(wsfe)
    assert cab == {"CantReg": 1, "PtoVta": 1, "CbteTipo": 11}
    assert det["CbteDesde"] == det["CbteHasta"] == 42
    assert det["CbteFch"] == "20261019"
    assert det["ImpTotal"] == det["ImpNeto"] == 1500.5
    assert det["ImpIVA"] == det["ImpTotConc"] == det["ImpOpEx"] == det["ImpTrib"] == 0
    assert "Iva" not in det
    assert det["DocTipo"] == 99 and det["DocNro"] == 0
    assert det["CondicionIVAReceptorId"] == 5
    assert det["MonId"] == "PES" and det["MonCotiz"] == 1
    assert "FchServDesde" not in det

    auth = wsfe.fake.requests[0][1]
    assert auth == {"Token": "TOKEN", "Sign": "SIGN", "Cuit": 20123456780}


def test_factura_c_servicios_receptor_identificado(wsfe):
    wsfe.create_invoice_c(
        2,
        100,
        concepto=Concepto.SERVICIOS,
        doc_tipo=DocTipo.CUIT,
        doc_nro=30712345671,
        condicion_iva_receptor=CondicionIVAReceptor.IVA_RESPONSABLE_INSCRIPTO,
        fecha=date(2026, 10, 19),
        servicio_desde=date(2026, 10, 1),
        servicio_hasta=date(2026, 10, 31),
    )
    _, det = _detalle(wsfe)
    assert det["Concepto"] == 2
    assert det["DocTipo"] == 80 and det["DocNro"] == 30712345671
    assert det["CondicionIVAReceptorId"] == 1
    assert det["FchServDesde"] == "20261001"
    assert det["FchServHasta"] == "20261031"
    assert det["FchVtoPago"] == "20261019"


def test_primer_comprobante(wsfe):
    wsfe.fake.last = 0
    assert wsfe.create_invoice_c(1, 10).numero == 1


def test_rechazo(wsfe):
    wsfe.fake.resultado = "R"
    wsfe.fake.obs = [(10016, "El numero de comprobante no es el proximo")]
    with pytest.raises(WSFEError) as exc:
        wsfe.create_invoice_c(1, 10)
    assert exc.value.codes == [10016]


def test_error_general(wsfe):
    wsfe.fake.resultado = "R"
    wsfe.fake.errores = [(600, "No se corresponden token y firma")]
    with pytest.raises(WSFEError, match="600"):
        wsfe.create_invoice_c(1, 10)


def test_aprobado_con_observaciones(wsfe):
    wsfe.fake.obs = [(10217, "observación de prueba")]
    factura = wsfe.create_invoice_c(1, 10)
    assert [o.code for o in factura.observaciones] == [10217]


def test_last_voucher_number_error(wsfe):
    def fail(**kwargs):
        return _t(
            "FERecuperaLastCbteResponse",
            CbteNro=0,
            Errors=_array("Err", [(601, "CUIT representada no incluida")]),
        )

    wsfe.fake.FECompUltimoAutorizado = fail
    with pytest.raises(WSFEError, match="601"):
        wsfe.last_voucher_number(1, CbteTipo.FACTURA_C)


def test_respuesta_real_de_homologacion(wsfe):
    """Estructura de una respuesta real de wswhomo (comprobante aprobado), con datos ficticios."""
    real = _t(
        "FECAEResponse",
        FeCabResp=_t(
            "FECAECabResponse",
            Cuit=20123456780,
            PtoVta=1,
            CbteTipo=11,
            FchProceso="20261019201144",
            CantReg=1,
            Resultado="A",
            Reproceso="N",
        ),
        FeDetResp=_t(
            "ArrayOfFECAEDetResponse",
            FECAEDetResponse=[
                _t(
                    "FECAEDetResponse",
                    Concepto=1,
                    DocTipo=99,
                    DocNro=0,
                    CbteDesde=2,
                    CbteHasta=2,
                    CbteFch="20261019",
                    Resultado="A",
                    Observaciones=None,
                    CAE="70000000000001",
                    CAEFchVto="20261029",
                )
            ],
        ),
    )
    wsfe.fake.last = 1
    wsfe.fake.FECAESolicitar = lambda **kwargs: real
    factura = wsfe.create_invoice_c(1, "1000.00")
    assert factura.numero == 2
    assert factura.cae == "70000000000001"
    assert factura.cae_vencimiento == date(2026, 10, 29)


def test_respuesta_irreconocible_no_se_reporta_como_rechazo(wsfe):
    wsfe.fake.FECAESolicitar = lambda **kwargs: NS(FeDetResp=None, Errors=None, Events=None)
    with pytest.raises(ArcaError, match="podría haber sido autorizado") as exc:
        wsfe.create_invoice_c(1, 10)
    assert not isinstance(exc.value, WSFEError)


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"importe_total": 0}, ValueError),
        ({"importe_total": "10.001"}, ValueError),
        ({"importe_total": "abc"}, ValueError),
        ({"importe_total": 10.5}, TypeError),
        ({"importe_total": 10, "doc_nro": 123}, ValueError),
        ({"importe_total": 10, "doc_tipo": DocTipo.DNI, "doc_nro": 30111222}, ValueError),
        ({"importe_total": 10, "doc_tipo": DocTipo.DNI, "doc_nro": 0}, ValueError),
        ({"importe_total": 10, "concepto": Concepto.SERVICIOS}, ValueError),
        (
            {
                "importe_total": 10,
                "concepto": Concepto.SERVICIOS,
                "servicio_desde": date(2026, 10, 31),
                "servicio_hasta": date(2026, 10, 1),
            },
            ValueError,
        ),
    ],
)
def test_validaciones_locales(wsfe, kwargs, error):
    with pytest.raises(error):
        wsfe.create_invoice_c(1, **kwargs)
    assert not any(r[0] == "cae" for r in wsfe.fake.requests)
