from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace as NS

import pytest

from arca_client import (
    WSAA,
    WSFE,
    CbteTipo,
    Concepto,
    CondicionIVAReceptor,
    DocTipo,
    MemoryTicketCache,
    WSFEError,
)
from arca_client.wsaa.ticket import AccessTicket


class FakeService:
    def __init__(self, last=41, resultado="A", errores=None, obs=None):
        self.last = last
        self.resultado = resultado
        self.errores = errores
        self.obs = obs
        self.requests = []

    def FECompUltimoAutorizado(self, Auth, PtoVta, CbteTipo):
        self.requests.append(("ultimo", Auth, PtoVta, CbteTipo))
        return NS(PtoVta=PtoVta, CbteTipo=CbteTipo, CbteNro=self.last, Errors=None)

    def FECAESolicitar(self, Auth, FeCAEReq):
        self.requests.append(("cae", Auth, FeCAEReq))
        det = FeCAEReq["FeDetReq"]["FECAEDetRequest"][0]
        obs = NS(Observaciones=[NS(Code=c, Msg=m) for c, m in self.obs]) if self.obs else None
        errors = NS(Err=[NS(Code=c, Msg=m) for c, m in self.errores]) if self.errores else None
        aprobado = self.resultado == "A"
        return NS(
            FeCabResp=NS(Resultado=self.resultado),
            FeDetResp=NS(
                FEDetResponse=[
                    NS(
                        CbteDesde=det["CbteDesde"],
                        CbteFch=det["CbteFch"],
                        Resultado=self.resultado,
                        CAE="76123456789012" if aprobado else None,
                        CAEFchVto="20261029" if aprobado else None,
                        Obs=obs,
                    )
                ]
            ),
            Errors=errors,
            Events=None,
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
        return NS(CbteNro=0, Errors=NS(Err=[NS(Code=601, Msg="CUIT representada no incluida")]))

    wsfe.fake.FECompUltimoAutorizado = fail
    with pytest.raises(WSFEError, match="601"):
        wsfe.last_voucher_number(1, CbteTipo.FACTURA_C)


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
