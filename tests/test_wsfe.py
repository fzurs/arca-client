from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import requests
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
    WSFEConnectionError,
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
        self.autorizados = {}  # numero -> detalle enviado, como la base de ARCA
        # Simulación de cortes de comunicación en FECAESolicitar:
        #   "antes": el pedido no llega a ARCA; "despues": ARCA autoriza y se pierde la respuesta.
        self.corte = None
        self.consultas_fallidas = 0  # cuántas llamadas a FECompConsultar fallan por red
        # Cuántos FECAESolicitar pierden la carrera: otro proceso emite justo antes.
        self.competidores = 0

    def FECompUltimoAutorizado(self, Auth, PtoVta, CbteTipo):
        self.requests.append(("ultimo", Auth, PtoVta, CbteTipo))
        return _t("FERecuperaLastCbteResponse", PtoVta=PtoVta, CbteTipo=CbteTipo, CbteNro=self.last)

    def FECAESolicitar(self, Auth, FeCAEReq):
        self.requests.append(("cae", Auth, FeCAEReq))
        det = FeCAEReq["FeDetReq"]["FECAEDetRequest"][0]
        if self.corte == "antes":
            raise requests.ConnectionError("conexión rechazada")
        if self.competidores:
            self.competidores -= 1
            self.last += 1
            self.autorizados[self.last] = {**det, "CbteDesde": self.last, "DocNro": 99}
        resultado, obs, errores = self.resultado, self.obs, self.errores
        if det["CbteDesde"] != self.last + 1:  # validación de ARCA
            # Homologación lo devuelve en Errors/Err, no en Observaciones/Obs.
            resultado = "R"
            errores = [(10016, "El numero o fecha del comprobante no se corresponde")]
        aprobado = resultado == "A"
        if aprobado:
            self.autorizados[det["CbteDesde"]] = det
            self.last = det["CbteDesde"]
        if self.corte == "despues":
            raise requests.Timeout("se agotó el tiempo de espera")
        detalle = _t(
            "FECAEDetResponse",
            Concepto=det["Concepto"],
            DocTipo=det["DocTipo"],
            DocNro=det["DocNro"],
            CbteDesde=det["CbteDesde"],
            CbteHasta=det["CbteHasta"],
            CbteFch=det["CbteFch"],
            Resultado=resultado,
            Observaciones=_array("Obs", obs),
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
                Resultado=resultado,
                Reproceso="N",
            ),
            FeDetResp=_t("ArrayOfFECAEDetResponse", FECAEDetResponse=[detalle]),
            Errors=_array("Err", errores),
        )

    def FECompConsultar(self, Auth, FeCompConsReq):
        self.requests.append(("consulta", Auth, FeCompConsReq))
        if self.consultas_fallidas:
            self.consultas_fallidas -= 1
            raise requests.ConnectionError("sin conexión")
        det = self.autorizados.get(FeCompConsReq["CbteNro"])
        if det is None:
            return _t(
                "FECompConsultaResponse",
                Errors=_array("Err", [(602, "No existen datos en nuestros registros")]),
            )
        campos = (
            "Concepto DocTipo DocNro CbteDesde CbteHasta CbteFch ImpTotal ImpTotConc ImpNeto "
            "ImpOpEx ImpTrib ImpIVA FchServDesde FchServHasta FchVtoPago MonId MonCotiz "
            "CondicionIVAReceptorId"
        ).split()
        return _t(
            "FECompConsultaResponse",
            ResultGet=_t(
                "FECompConsResponse",
                **{k: det.get(k) for k in campos},
                Resultado="A",
                CodAutorizacion="76123456789012",
                EmisionTipo="CAE",
                FchVto="20261029",
                FchProceso="20261019201304",
                PtoVta=FeCompConsReq["PtoVta"],
                CbteTipo=FeCompConsReq["CbteTipo"],
            ),
        )


@pytest.fixture
def wsfe(cert_pem, key_pem):
    cache = MemoryTicketCache()
    wsaa = WSAA(cert_pem, key_pem, cache=cache)
    now = datetime.now(timezone.utc)
    ticket = AccessTicket("TOKEN", "SIGN", now, now + timedelta(hours=12))
    cache.set(wsaa._cache_key("wsfe"), ticket)
    client = WSFE(wsaa, recovery_delay=0)
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
    # El último autorizado no cambió (el rechazo es por la fecha, por ejemplo): no se reintenta.
    assert len(_pedidos_cae(wsfe)) == 1


def _pedidos_cae(wsfe):
    return [r for r in wsfe.fake.requests if r[0] == "cae"]


def test_otro_proceso_usa_el_numero_se_reintenta_con_el_siguiente(wsfe):
    wsfe.fake.competidores = 1  # otro proceso emite el 42 entre nuestra consulta y el pedido
    factura = wsfe.create_invoice_c(1, 10)
    assert factura.numero == 43
    assert factura.cae == "76123456789012"
    pedidos = [r["FeDetReq"]["FECAEDetRequest"][0]["CbteDesde"] for _, _, r in _pedidos_cae(wsfe)]
    assert pedidos == [42, 43]
    assert wsfe.fake.autorizados[42]["DocNro"] == 99  # el del otro proceso quedó intacto


def test_numeracion_agota_reintentos(wsfe):
    wsfe.fake.competidores = 10
    with pytest.raises(WSFEError) as exc:
        wsfe.create_invoice_c(1, 10)
    assert exc.value.codes == [10016]
    assert len(_pedidos_cae(wsfe)) == wsfe.numbering_attempts


def test_otros_rechazos_no_se_reintentan(wsfe):
    wsfe.fake.resultado = "R"
    wsfe.fake.obs = [(10015, "documento inválido")]
    with pytest.raises(WSFEError):
        wsfe.create_invoice_c(1, 10)
    assert len(_pedidos_cae(wsfe)) == 1
    assert sum(r[0] == "ultimo" for r in wsfe.fake.requests) == 1


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


def _consultas(wsfe):
    return [r for r in wsfe.fake.requests if r[0] == "consulta"]


def test_get_voucher(wsfe):
    factura = wsfe.create_invoice_c(1, "1500.50", fecha=date(2026, 10, 19))
    consultada = wsfe.get_voucher(1, CbteTipo.FACTURA_C, factura.numero)
    assert consultada.cae == "76123456789012"
    assert consultada.importe_total == Decimal("1500.50")
    assert consultada.fecha == date(2026, 10, 19)
    assert consultada.condicion_iva_receptor is CondicionIVAReceptor.CONSUMIDOR_FINAL
    assert wsfe.get_voucher(1, CbteTipo.FACTURA_C, 999) is None


def test_get_voucher_otro_error(wsfe):
    wsfe.fake.FECompConsultar = lambda **kw: _t(
        "FECompConsultaResponse", Errors=_array("Err", [(600, "token inválido")])
    )
    with pytest.raises(WSFEError, match="600"):
        wsfe.get_voucher(1, CbteTipo.FACTURA_C, 1)


def test_corte_despues_de_autorizar_recupera_el_cae(wsfe):
    """ARCA autorizó pero se perdió la respuesta: no hay que emitir otra factura."""
    wsfe.fake.corte = "despues"
    factura = wsfe.create_invoice_c(1, "1500.50", fecha=date(2026, 10, 19))
    assert factura.numero == 42
    assert factura.cae == "76123456789012"
    assert factura.importe_total == Decimal("1500.50")
    assert len(_consultas(wsfe)) == 1
    assert list(wsfe.fake.autorizados) == [42]  # una sola factura emitida


def test_corte_antes_de_autorizar_es_seguro_reintentar(wsfe):
    wsfe.fake.corte = "antes"
    with pytest.raises(WSFEConnectionError) as exc:
        wsfe.create_invoice_c(1, 10)
    assert exc.value.safe_to_retry is True
    assert exc.value.numero == 42
    assert not wsfe.fake.autorizados

    wsfe.fake.corte = None
    assert wsfe.create_invoice_c(1, 10).numero == 42  # reintento: mismo número


def test_corte_sin_poder_verificar_no_es_seguro(wsfe):
    wsfe.fake.corte = "despues"
    wsfe.fake.consultas_fallidas = 99
    with pytest.raises(WSFEConnectionError, match="NO reintentes") as exc:
        wsfe.create_invoice_c(1, 10)
    assert exc.value.safe_to_retry is False
    assert (exc.value.punto_venta, exc.value.numero) == (1, 42)
    assert len(_consultas(wsfe)) == wsfe.recovery_attempts


def test_corte_verifica_tras_reintentos(wsfe):
    wsfe.fake.corte = "despues"
    wsfe.fake.consultas_fallidas = 2  # falla 2 veces, la tercera responde
    assert wsfe.create_invoice_c(1, 10).cae == "76123456789012"
    assert len(_consultas(wsfe)) == 3


def test_corte_con_datos_distintos_no_es_seguro(wsfe):
    wsfe.fake.corte = "antes"
    # Otro proceso ya usó el número 42 con otro importe.
    wsfe.fake.autorizados[42] = {
        "Concepto": 1,
        "DocTipo": 99,
        "DocNro": 0,
        "CbteDesde": 42,
        "CbteHasta": 42,
        "CbteFch": "20261019",
        "ImpTotal": 999.0,
        "CondicionIVAReceptorId": 5,
    }
    with pytest.raises(WSFEConnectionError, match="datos distintos") as exc:
        wsfe.create_invoice_c(1, 10, fecha=date(2026, 10, 19))
    assert exc.value.safe_to_retry is False


def test_respuesta_irreconocible_se_verifica(wsfe):
    original = wsfe.fake.FECAESolicitar

    def autoriza_pero_responde_raro(**kwargs):
        original(**kwargs)
        return NS(FeDetResp=None, Errors=None, Events=None)

    wsfe.fake.FECAESolicitar = autoriza_pero_responde_raro
    factura = wsfe.create_invoice_c(1, 10)
    assert factura.cae == "76123456789012"
    assert len(_consultas(wsfe)) == 1


def test_errores_antes_de_enviar_no_disparan_recuperacion(wsfe, monkeypatch):
    def sin_ticket(service):
        raise ArcaError("WSAA caído")

    monkeypatch.setattr(wsfe.wsaa, "get_ticket", sin_ticket)
    with pytest.raises(ArcaError, match="WSAA caído"):
        wsfe.create_invoice_c(1, 10)
    assert not _consultas(wsfe)


def test_rechazo_no_dispara_recuperacion(wsfe):
    wsfe.fake.resultado = "R"
    wsfe.fake.obs = [(10016, "numero no es el proximo")]
    with pytest.raises(WSFEError):
        wsfe.create_invoice_c(1, 10)
    assert not _consultas(wsfe)


def test_transport_tiene_timeout_de_operacion():
    from arca_client.environments import Environment
    from arca_client.transport import make_transport

    transport = make_transport(Environment.HOMOLOGACION, 12)
    assert transport.operation_timeout == 12
    assert transport.load_timeout == 12


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
