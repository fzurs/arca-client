from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import zeep
from zeep.exceptions import Fault

from arca_client import (
    WSAA,
    WSPCI,
    ArcaError,
    Categoria,
    CondicionIVAReceptor,
    Constancia,
    Domicilio,
    Impuesto,
    MemoryTicketCache,
    PersonaNoEncontrada,
    TipoPersona,
    WSAAError,
    WSPCIError,
)
from arca_client.environments import Environment, wspci_wsdl
from arca_client.wsaa.ticket import AccessTicket
from arca_client.wspci.client import SERVICE, _parsear_constancia

# Tipos del WSDL real: si un nombre de campo no existe, zeep lanza TypeError.
WSDL = Path(__file__).parent / "fixtures" / "wspci.wsdl"
_ZEEP = zeep.Client(str(WSDL))

# Datos inventados.
CUIT_FISICA = 20123456786
CUIT_JURIDICA = 30712345671
NO_EXISTE = "No existe persona con ese Id"


def _t(name, **kwargs):
    return _ZEEP.get_type(f"ns0:{name}")(**kwargs)


def _impuesto(id_impuesto, estado="ACTIVO", descripcion="UN IMPUESTO", periodo=202001):
    return _t(
        "impuesto",
        idImpuesto=id_impuesto,
        estadoImpuesto=estado,
        descripcionImpuesto=descripcion,
        periodo=periodo,
    )


def _domicilio(**kwargs):
    datos = dict(
        direccion="AV FICTICIA 123",
        localidad="VILLA INVENTADA",
        descripcionProvincia="BUENOS AIRES",
        codPostal="1234",
        idProvincia=1,
        tipoDomicilio="FISCAL",
    )
    return _t("domicilio", **(datos | kwargs))


def _fisica(**kwargs):
    datos = dict(
        idPersona=CUIT_FISICA,
        apellido="PEREZ",
        nombre="JUAN",
        tipoPersona="FISICA",
        tipoClave="CUIT",
        estadoClave="ACTIVO",
        esSucesion="NO",
        domicilioFiscal=_domicilio(),
    )
    return _t("datosGenerales", **(datos | kwargs))


def _juridica():
    return _t(
        "datosGenerales",
        idPersona=CUIT_JURIDICA,
        razonSocial="EMPRESA INVENTADA SA",
        tipoPersona="JURIDICA",
        tipoClave="CUIT",
        estadoClave="ACTIVO",
        domicilioFiscal=_domicilio(),
    )


def _regimen(*impuestos):
    return _t("datosRegimenGeneral", impuesto=list(impuestos))


def _monotributo(*impuestos, categoria=None):
    return _t("datosMonotributo", impuesto=list(impuestos), categoriaMonotributo=categoria)


def _persona(**kwargs):
    return _t("personaReturn", **kwargs)


def _con_impuestos(general=(), monotributo=(), **kwargs):
    return _parsear_constancia(
        _persona(
            datosGenerales=_fisica(),
            datosRegimenGeneral=_regimen(*general) if general else None,
            datosMonotributo=_monotributo(*monotributo) if monotributo else None,
            **kwargs,
        )
    )


# --- _parsear_constancia ----------------------------------------------------------


def test_persona_fisica_responsable_inscripta():
    c = _con_impuestos(general=[_impuesto(30, descripcion="IVA", periodo=199807)])
    assert c.cuit == str(CUIT_FISICA)
    assert c.tipo_persona is TipoPersona.FISICA
    assert c.tipo_clave == "CUIT"
    assert (c.apellido, c.nombre, c.razon_social) == ("PEREZ", "JUAN", None)
    assert c.denominacion == "PEREZ JUAN"
    assert c.activa
    assert c.sucesion is False
    assert c.fecha_fallecimiento is None
    assert c.impuestos == [Impuesto(30, "IVA", "ACTIVO", 199807)]
    assert c.impuestos_monotributo == []
    assert c.categoria_monotributo is None
    assert c.domicilio_fiscal == Domicilio(
        "AV FICTICIA 123", "VILLA INVENTADA", "BUENOS AIRES", "1234", None
    )
    assert c.condicion_iva is CondicionIVAReceptor.IVA_RESPONSABLE_INSCRIPTO
    assert c.condicion_iva == 1


def test_persona_juridica_solo_con_razon_social():
    c = _parsear_constancia(
        _persona(datosGenerales=_juridica(), datosRegimenGeneral=_regimen(_impuesto(30)))
    )
    assert c.tipo_persona is TipoPersona.JURIDICA
    assert c.denominacion == "EMPRESA INVENTADA SA"
    assert c.nombre is None and c.apellido is None
    assert c.condicion_iva is CondicionIVAReceptor.IVA_RESPONSABLE_INSCRIPTO


def test_monotributista_con_categoria():
    categoria = _t(
        "categoria",
        idCategoria=3,
        descripcionCategoria="C LOCACIONES DE SERVICIOS",
        idImpuesto=20,
        periodo=202401,
    )
    c = _parsear_constancia(
        _persona(
            datosGenerales=_fisica(),
            datosMonotributo=_monotributo(_impuesto(20), categoria=categoria),
        )
    )
    assert c.categoria_monotributo == Categoria(3, "C LOCACIONES DE SERVICIOS", 202401)
    assert [i.id for i in c.impuestos_monotributo] == [20]
    assert c.impuestos == []
    assert c.condicion_iva is CondicionIVAReceptor.RESPONSABLE_MONOTRIBUTO
    assert c.condicion_iva == 6


@pytest.mark.parametrize(
    "kwargs",
    [
        {},  # sin impuestos: no se deduce "consumidor final"
        {"general": [_impuesto(30)], "monotributo": [_impuesto(20)]},
        {"general": [_impuesto(30), _impuesto(20)]},
        {"monotributo": [_impuesto(30), _impuesto(20)]},
        {"general": [_impuesto(30, estado="BAJA")]},
        {"general": [_impuesto(30, estado="activo")]},
        {"monotributo": [_impuesto(20, estado="BAJA")]},
        {"general": [_impuesto(20)]},  # 20 fuera de la sección de monotributo
        {"monotributo": [_impuesto(30)]},  # 30 fuera de régimen general
        {"general": [_impuesto(32)]},  # sin confirmar que sea IVA exento
        {"general": [_impuesto(11)]},
    ],
)
def test_condicion_iva_no_adivina(kwargs):
    assert _con_impuestos(**kwargs).condicion_iva is None


def test_estado_ausente_cuenta_como_vigente():
    c = _con_impuestos(general=[_impuesto(30, estado=None)])
    assert c.condicion_iva is CondicionIVAReceptor.IVA_RESPONSABLE_INSCRIPTO


def test_impuesto_no_vigente_no_contradice():
    c = _con_impuestos(general=[_impuesto(30, estado="BAJA")], monotributo=[_impuesto(20)])
    assert c.condicion_iva is CondicionIVAReceptor.RESPONSABLE_MONOTRIBUTO


@pytest.mark.parametrize(
    ("campo", "tipo", "atributo"),
    [
        ("errorRegimenGeneral", "errorRegimenGeneral", "errores_regimen_general"),
        ("errorMonotributo", "errorMonotributo", "errores_monotributo"),
    ],
)
def test_error_de_seccion_se_guarda_y_anula_condicion_iva(campo, tipo, atributo):
    # Mensaje en `error[]`, en `mensaje` o sin texto: en todos los casos falta información.
    for error, esperado in [
        (_t(tipo, error=["falla"]), ["falla"]),
        (_t(tipo, mensaje="falla"), ["falla"]),
        (_t(tipo, error=["falla", None], mensaje="falla"), ["falla"]),
    ]:
        c = _con_impuestos(general=[_impuesto(30)], **{campo: error})
        assert getattr(c, atributo) == esperado
        assert c.condicion_iva is None
    c = _con_impuestos(general=[_impuesto(30)], **{campo: _t(tipo)})
    assert getattr(c, atributo) and c.condicion_iva is None


def test_error_constancia_devuelve_constancia_con_datos_del_error():
    c = _parsear_constancia(
        _persona(
            errorConstancia=_t(
                "errorConstancia",
                error=["La CUIT tiene limitaciones"],
                idPersona=CUIT_FISICA,
                nombre="JUAN",
                apellido="PEREZ",
            )
        )
    )
    assert c.cuit == str(CUIT_FISICA)
    assert c.denominacion == "PEREZ JUAN"
    assert c.errores_constancia == ["La CUIT tiene limitaciones"]
    assert not c.activa
    assert c.condicion_iva is None


def test_error_constancia_junto_con_datos_generales():
    c = _parsear_constancia(
        _persona(
            datosGenerales=_fisica(),
            datosRegimenGeneral=_regimen(_impuesto(30)),
            errorConstancia=_t("errorConstancia", error=[NO_EXISTE]),
        )
    )
    assert c.errores_constancia == [NO_EXISTE]
    assert c.denominacion == "PEREZ JUAN"
    assert c.condicion_iva is None


def test_error_de_seccion_sin_datos_generales_devuelve_constancia():
    c = _parsear_constancia(
        _persona(errorMonotributo=_t("errorMonotributo", mensaje="falla")), str(CUIT_FISICA)
    )
    assert c.cuit == str(CUIT_FISICA)
    assert c.errores_monotributo == ["falla"]


@pytest.mark.parametrize("error", [NO_EXISTE, "  no  existe PERSONA con ese id.  "])
def test_persona_inexistente_por_error_constancia(error):
    with pytest.raises(PersonaNoEncontrada) as e:
        _parsear_constancia(_persona(errorConstancia=_t("errorConstancia", error=[error])))
    assert isinstance(e.value, WSPCIError)
    assert e.value.errores


@pytest.mark.parametrize(
    "respuesta",
    [
        None,
        SimpleNamespace(),
        _persona(),
        _persona(metadata=_t("metadata", servidor="srv")),
        _persona(datosGenerales=_t("datosGenerales")),
        _persona(datosRegimenGeneral=_regimen(_impuesto(30))),
    ],
)
def test_respuesta_vacia_lanza_arca_error(respuesta):
    with pytest.raises(ArcaError) as e:
        _parsear_constancia(respuesta, str(CUIT_FISICA))
    assert not isinstance(e.value, WSPCIError)


def test_error_de_respuesta_no_incluye_datos_personales():
    respuesta = _persona(datosRegimenGeneral=_regimen(_impuesto(30, descripcion="DATO PRIVADO")))
    with pytest.raises(ArcaError) as e:
        _parsear_constancia(respuesta)
    assert "PRIVADO" not in str(e.value)


def test_sin_id_persona_no_inventa_una_cuit():
    respuesta = _persona(errorConstancia=_t("errorConstancia", error=["otra cosa"]))
    with pytest.raises(ArcaError, match="idPersona"):
        _parsear_constancia(respuesta)
    assert _parsear_constancia(respuesta, str(CUIT_FISICA)).cuit == str(CUIT_FISICA)


def test_acepta_simple_namespace_con_campos_ausentes():
    c = _parsear_constancia(
        SimpleNamespace(
            datosGenerales=SimpleNamespace(idPersona=CUIT_FISICA, apellido="PEREZ"),
            datosRegimenGeneral=SimpleNamespace(
                impuesto=[None, SimpleNamespace(idImpuesto=30), SimpleNamespace(periodo=1)]
            ),
        )
    )
    assert c.denominacion == "PEREZ"
    assert c.tipo_persona is None and c.domicilio_fiscal is None and not c.activa
    assert c.impuestos == [Impuesto(30, None, None, None)]
    assert c.condicion_iva is CondicionIVAReceptor.IVA_RESPONSABLE_INSCRIPTO


def test_tipo_persona_desconocido_es_none():
    c = _parsear_constancia(_persona(datosGenerales=_fisica(tipoPersona="OTRA")))
    assert c.tipo_persona is None


def test_clave_inactiva_no_esta_activa():
    assert not _parsear_constancia(_persona(datosGenerales=_fisica(estadoClave="INACTIVO"))).activa


def test_sucesion_con_fecha_de_fallecimiento():
    fecha = datetime(2020, 5, 1, tzinfo=timezone.utc)
    c = _parsear_constancia(
        _persona(datosGenerales=_fisica(esSucesion="SI", fechaFallecimiento=fecha))
    )
    assert c.sucesion is True
    assert c.fecha_fallecimiento == fecha


def test_denominacion_sin_datos():
    c = _parsear_constancia(_persona(datosGenerales=_t("datosGenerales", idPersona=CUIT_FISICA)))
    assert c.denominacion is None
    assert isinstance(c, Constancia)


# --- Domicilio --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "esperado"),
    [
        ({}, "AV FICTICIA 123, VILLA INVENTADA, 1234, BUENOS AIRES"),
        ({"localidad": None}, "AV FICTICIA 123, 1234, BUENOS AIRES"),
        ({"direccion": None}, "VILLA INVENTADA, 1234, BUENOS AIRES"),
        ({"localidad": "  "}, "AV FICTICIA 123, 1234, BUENOS AIRES"),
        (
            {"direccion": "AV\nFICTICIA  123"},
            "AV FICTICIA 123, VILLA INVENTADA, 1234, BUENOS AIRES",
        ),
        (
            {"datoAdicional": "PISO 2"},
            "AV FICTICIA 123, PISO 2, VILLA INVENTADA, 1234, BUENOS AIRES",
        ),
    ],
)
def test_domicilio_en_una_linea(kwargs, esperado):
    c = _parsear_constancia(_persona(datosGenerales=_fisica(domicilioFiscal=_domicilio(**kwargs))))
    assert str(c.domicilio_fiscal) == esperado


def test_domicilio_vacio_es_none():
    c = _parsear_constancia(_persona(datosGenerales=_fisica(domicilioFiscal=_t("domicilio"))))
    assert c.domicilio_fiscal is None


# --- Cliente ----------------------------------------------------------------------


class FakeService:
    def __init__(self, respuesta=None, error=None):
        self.respuesta = respuesta
        self.error = error
        self.pedidos = []

    def getPersona_v2(self, **kwargs):
        self.pedidos.append(kwargs)
        if self.error:
            raise self.error
        return self.respuesta

    def dummy(self):
        if self.error:
            raise self.error
        return _t("dummyReturn", appserver="OK", authserver="OK", dbserver="OK")


@pytest.fixture
def wsaa(cert_pem, key_pem):
    cache = MemoryTicketCache()
    wsaa = WSAA(cert_pem, key_pem, cache=cache)
    now = datetime.now(timezone.utc)
    # Si se pidiera otro servicio, no estaría en el caché e iría a la red: hacemos que falle.
    cache.set(
        wsaa._cache_key(SERVICE), AccessTicket("TOKEN", "SIGN", now, now + timedelta(hours=12))
    )

    def sin_red(service):
        raise AssertionError(f"Se pidió un ticket nuevo para {service}")

    wsaa.login = sin_red
    wsaa.tickets_pedidos = []
    get_ticket = wsaa.get_ticket

    def espia(service, **kwargs):
        wsaa.tickets_pedidos.append(service)
        return get_ticket(service, **kwargs)

    wsaa.get_ticket = espia
    return wsaa


@pytest.fixture
def wspci(wsaa):
    cliente = WSPCI(wsaa)
    cliente.fake = FakeService(_persona(datosGenerales=_fisica()))
    cliente._service = lambda: cliente.fake
    return cliente


def test_el_servicio_es_el_de_la_constancia():
    assert SERVICE == "ws_sr_constancia_inscripcion"


@pytest.mark.parametrize(
    ("env", "host"),
    [
        (Environment.HOMOLOGACION, "https://awshomo.afip.gov.ar/"),
        (Environment.PRODUCCION, "https://aws.afip.gov.ar/"),
    ],
)
def test_urls_del_wsdl(env, host):
    assert wspci_wsdl(env).startswith(host) and wspci_wsdl(env).endswith("personaServiceA5?WSDL")


def test_cuit_con_guiones_se_envia_como_entero(wspci, wsaa):
    c = wspci.get_constancia("20-12345678-6")
    assert c.cuit == "20123456786"
    assert wspci.fake.pedidos == [
        {
            "token": "TOKEN",
            "sign": "SIGN",
            "cuitRepresentada": 20123456780,  # la del certificado de prueba
            "idPersona": 20123456786,
        }
    ]
    assert wsaa.tickets_pedidos == ["ws_sr_constancia_inscripcion"]


def test_cuit_representada_explicita(wsaa):
    cliente = WSPCI(wsaa, cuit="30-71234567-1")
    assert cliente._auth()["cuitRepresentada"] == CUIT_JURIDICA


def test_cuit_entera(wspci):
    wspci.get_constancia(CUIT_FISICA)
    assert wspci.fake.pedidos[0]["idPersona"] == CUIT_FISICA


@pytest.mark.parametrize("cuit", ["20-12345678-0", "123", "", "no es", None, 20123456780])
def test_cuit_invalida_no_pide_ticket_ni_llama(wspci, wsaa, cuit):
    with pytest.raises(ValueError):
        wspci.get_constancia(cuit)
    assert wsaa.tickets_pedidos == []
    assert wspci.fake.pedidos == []


def test_dummy_no_pide_ticket(wspci, wsaa):
    assert wspci.dummy() == {"appserver": "OK", "authserver": "OK", "dbserver": "OK"}
    assert wsaa.tickets_pedidos == []


def test_fault_persona_inexistente(wspci):
    wspci.fake.error = Fault(NO_EXISTE)
    with pytest.raises(PersonaNoEncontrada) as e:
        wspci.get_constancia(CUIT_FISICA)
    assert e.value.errores == [NO_EXISTE]


def test_otro_fault(wspci):
    wspci.fake.error = Fault("Error interno")
    with pytest.raises(WSPCIError) as e:
        wspci.get_constancia(CUIT_FISICA)
    assert not isinstance(e.value, PersonaNoEncontrada)
    assert e.value.errores == ["Error interno"]


def test_fault_en_dummy(wspci):
    wspci.fake.error = Fault("Error interno")
    with pytest.raises(WSPCIError):
        wspci.dummy()


def test_error_de_wsaa_se_propaga_sin_envolver(wspci, wsaa):
    def falla(service, **kwargs):
        raise WSAAError("coe.notAuthorized", "no autorizado")

    wsaa.get_ticket = falla
    with pytest.raises(WSAAError):
        wspci.get_constancia(CUIT_FISICA)
    assert wspci.fake.pedidos == []


def test_error_de_conexion_se_propaga_sin_envolver(wspci):
    wspci.fake.error = ConnectionError("sin red")
    with pytest.raises(ConnectionError):
        wspci.get_constancia(CUIT_FISICA)


# --- Con el WSDL real, sin red -----------------------------------------------------


class FakeTransport(zeep.Transport):
    """Devuelve una respuesta SOAP armada a mano en lugar de ir a la red."""

    def __init__(self, cuerpo: str, status: int = 200):
        super().__init__()
        self.cuerpo = cuerpo
        self.status = status
        self.enviado = b""

    def post(self, address, message, headers):
        self.enviado = message
        return SimpleNamespace(
            status_code=self.status,
            content=(
                '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
                f"<soap:Body>{self.cuerpo}</soap:Body></soap:Envelope>"
            ).encode(),
            headers={"Content-Type": "text/xml"},
            encoding="utf-8",
        )


def _con_wsdl(wsaa, cuerpo, status=200):
    cliente = WSPCI(wsaa)
    transport = FakeTransport(cuerpo, status)
    cliente._soap = zeep.Client(str(WSDL), transport=transport)
    return cliente, transport


NS = 'xmlns:ns2="http://a5.soap.ws.server.puc.sr/"'


def test_respuesta_xml_de_get_persona(wsaa):
    cliente, transport = _con_wsdl(
        wsaa,
        f"""<ns2:getPersona_v2Response {NS}><personaReturn>
          <datosGenerales>
            <apellido>PEREZ</apellido><esSucesion>SI</esSucesion><estadoClave>ACTIVO</estadoClave>
            <fechaFallecimiento>2020-05-01T00:00:00-03:00</fechaFallecimiento>
            <idPersona>{CUIT_FISICA}</idPersona>
            <nombre>JUAN</nombre><tipoPersona>FISICA</tipoPersona>
          </datosGenerales>
          <datosRegimenGeneral>
            <impuesto>
              <descripcionImpuesto>IVA</descripcionImpuesto>
              <estadoImpuesto>ACTIVO</estadoImpuesto><idImpuesto>30</idImpuesto>
            </impuesto>
          </datosRegimenGeneral>
        </personaReturn></ns2:getPersona_v2Response>""",
    )
    c = cliente.get_constancia(CUIT_FISICA)
    assert c.denominacion == "PEREZ JUAN"
    assert c.sucesion and c.fecha_fallecimiento.year == 2020
    assert c.condicion_iva is CondicionIVAReceptor.IVA_RESPONSABLE_INSCRIPTO
    enviado = transport.enviado.decode()
    assert f"<idPersona>{CUIT_FISICA}</idPersona>" in enviado
    assert "<cuitRepresentada>20123456780</cuitRepresentada>" in enviado
    assert "<token>TOKEN</token>" in enviado


def test_respuesta_xml_sin_persona_return(wsaa):
    cliente, _ = _con_wsdl(wsaa, f"<ns2:getPersona_v2Response {NS}/>")
    with pytest.raises(ArcaError):
        cliente.get_constancia(CUIT_FISICA)


def test_respuesta_xml_de_dummy(wsaa):
    cliente, _ = _con_wsdl(
        wsaa,
        f"""<ns2:dummyResponse {NS}><return><appserver>OK</appserver>
        <authserver>OK</authserver><dbserver>OK</dbserver></return></ns2:dummyResponse>""",
    )
    assert cliente.dummy() == {"appserver": "OK", "authserver": "OK", "dbserver": "OK"}


def test_fault_xml(wsaa):
    cliente, _ = _con_wsdl(
        wsaa,
        f"<soap:Fault><faultcode>soap:Server</faultcode><faultstring>{NO_EXISTE}</faultstring>"
        "</soap:Fault>",
        status=500,
    )
    with pytest.raises(PersonaNoEncontrada):
        cliente.get_constancia(CUIT_FISICA)
