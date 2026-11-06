"""Cliente de WSPCI (Constancia de Inscripción).

Dada una CUIT, devuelve los datos del contribuyente que ARCA informa en la constancia:
nombre o razón social, tipo de persona, domicilio fiscal e impuestos. Es solo lectura.
"""

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum

import zeep
from zeep.exceptions import Fault

from ..certificates import cuit_from_certificate, is_valid_cuit, normalize_cuit
from ..environments import wspci_wsdl
from ..exceptions import ArcaError, PersonaNoEncontrada, WSPCIError
from ..transport import make_transport
from ..wsaa import WSAA
from ..wsfe.types import CondicionIVAReceptor

SERVICE = "ws_sr_constancia_inscripcion"

_PERSONA_INEXISTENTE = "no existe persona con ese id"
_IMPUESTO_MONOTRIBUTO = 20
_IMPUESTO_IVA = 30
_ERROR_SIN_DETALLE = "ARCA informó un error sin detalle"


class TipoPersona(str, Enum):
    FISICA = "FISICA"
    JURIDICA = "JURIDICA"


@dataclass(frozen=True)
class Domicilio:
    direccion: str | None
    localidad: str | None
    provincia: str | None
    codigo_postal: str | None
    dato_adicional: str | None

    def __str__(self) -> str:
        """El domicilio en una línea, sin partes vacías."""
        partes = (
            self.direccion,
            self.dato_adicional,
            self.localidad,
            self.codigo_postal,
            self.provincia,
        )
        return ", ".join(texto for parte in partes if (texto := _texto(parte)))


@dataclass(frozen=True)
class Impuesto:
    id: int
    descripcion: str | None
    estado: str | None
    periodo: int | None


@dataclass(frozen=True)
class Categoria:
    id: int
    descripcion: str | None
    periodo: int | None


@dataclass(frozen=True)
class Constancia:
    """Datos del contribuyente que devuelve ``getPersona_v2``.

    Si un ``errores_*`` tiene contenido, la sección correspondiente puede estar incompleta:
    que falte un dato no significa que no exista.
    """

    cuit: str  # 11 dígitos, sin guiones
    tipo_persona: TipoPersona | None
    tipo_clave: str | None
    estado_clave: str | None
    nombre: str | None
    apellido: str | None
    razon_social: str | None
    domicilio_fiscal: Domicilio | None
    sucesion: bool
    fecha_fallecimiento: datetime | None
    impuestos: list[Impuesto]  # régimen general
    impuestos_monotributo: list[Impuesto]
    categoria_monotributo: Categoria | None
    errores_constancia: list[str]
    errores_regimen_general: list[str]
    errores_monotributo: list[str]

    @property
    def denominacion(self) -> str | None:
        """La razón social o, si no tiene, ``"APELLIDO NOMBRE"``."""
        if self.razon_social:
            return self.razon_social
        return " ".join(p for p in (self.apellido, self.nombre) if p) or None

    @property
    def activa(self) -> bool:
        return self.estado_clave == "ACTIVO"

    @property
    def condicion_iva(self) -> CondicionIVAReceptor | None:
        """Condición frente al IVA deducida de los impuestos, o ``None`` si no es segura.

        Esta derivación es nuestra, no de ARCA: el manual no define una tabla de condición
        frente al IVA. Por eso nunca adivina. Con errores, sin impuestos o con impuestos
        contradictorios devuelve ``None``; en particular, no deduce "consumidor final" por
        falta de datos (esa decisión queda en la aplicación).
        """
        if self.errores_constancia or self.errores_regimen_general or self.errores_monotributo:
            return None
        general = {i.id for i in self.impuestos if _vigente(i)}
        monotributo = {i.id for i in self.impuestos_monotributo if _vigente(i)}
        todos = general | monotributo
        if _IMPUESTO_MONOTRIBUTO in todos and _IMPUESTO_IVA in todos:
            return None
        if _IMPUESTO_MONOTRIBUTO in monotributo:
            return CondicionIVAReceptor.RESPONSABLE_MONOTRIBUTO
        if _IMPUESTO_IVA in general:
            return CondicionIVAReceptor.IVA_RESPONSABLE_INSCRIPTO
        return None


class WSPCI:
    """Consulta la constancia de inscripción de un contribuyente.

    >>> wsaa = WSAA("cert.crt", "private.key", Environment.HOMOLOGACION)
    >>> wspci = WSPCI(wsaa)
    >>> wspci.get_constancia("20-12345678-6").denominacion
    'PEREZ JUAN'
    """

    def __init__(self, wsaa: WSAA, cuit: str | int | None = None, *, timeout: float = 30) -> None:
        self.wsaa = wsaa
        self.env = wsaa.env
        cuit = cuit or cuit_from_certificate(wsaa.certificate)
        if not cuit:
            raise ArcaError("No se pudo obtener la CUIT del certificado; pasala con `cuit=`")
        self.cuit = int(normalize_cuit(str(cuit)))
        self.timeout = timeout
        self._soap: zeep.Client | None = None

    def get_constancia(self, cuit: str | int) -> Constancia:
        """Devuelve los datos de `cuit` (``getPersona_v2``).

        - ``ValueError``: la CUIT no es válida (no se llama a ARCA).
        - :class:`PersonaNoEncontrada`: ARCA no registra esa CUIT.
        - :class:`WSPCIError`: otro SOAP Fault de la operación.
        - :class:`ArcaError`: una respuesta sin datos ni errores.

        Una CUIT limitada o inactiva no lanza excepción: devuelve la :class:`Constancia` con
        sus ``errores_constancia``. ``WSAAError`` y los errores de conexión se propagan.
        """
        cuit = normalize_cuit(str(cuit))
        if not is_valid_cuit(cuit):
            raise ValueError(f"CUIT inválida: {cuit}")
        auth = self._auth()
        service = self._service()
        try:
            persona_return = service.getPersona_v2(**auth, idPersona=int(cuit))
        except Fault as fault:
            raise _error_de_fault(fault) from fault
        return _parsear_constancia(persona_return, cuit)

    def dummy(self) -> dict[str, str]:
        """Estado de los servidores de WSPCI (no requiere ticket)."""
        try:
            r = self._service().dummy()
        except Fault as fault:
            raise _error_de_fault(fault) from fault
        r = getattr(r, "return", r)
        return {c: getattr(r, c, None) or "" for c in ("appserver", "authserver", "dbserver")}

    # --- Infraestructura -------------------------------------------------------

    def _auth(self) -> dict:
        ticket = self.wsaa.get_ticket(SERVICE)
        return {"token": ticket.token, "sign": ticket.sign, "cuitRepresentada": self.cuit}

    def _service(self):
        if self._soap is None:
            transport = make_transport(self.env, self.timeout)
            self._soap = zeep.Client(wspci_wsdl(self.env), transport=transport)
        return self._soap.service


def _parsear_constancia(persona_return, cuit: str | None = None) -> Constancia:
    """Convierte ``personaReturn`` en una :class:`Constancia`.

    Acepta objetos de zeep o ``SimpleNamespace`` y tolera campos ausentes. `cuit` es la CUIT
    consultada: se usa solo si la respuesta no trae ``idPersona``.

    Los mensajes de error no incluyen la respuesta, porque tiene datos personales.
    """
    generales = getattr(persona_return, "datosGenerales", None)
    if not _tiene_datos_generales(generales):
        generales = None
    error_constancia = getattr(persona_return, "errorConstancia", None)
    errores_constancia = _errores(error_constancia)
    errores_regimen = _errores(getattr(persona_return, "errorRegimenGeneral", None))
    errores_monotributo = _errores(getattr(persona_return, "errorMonotributo", None))

    if generales is None:
        for error in errores_constancia:
            if _PERSONA_INEXISTENTE in _clave(error):
                raise PersonaNoEncontrada(errores_constancia)
        if not (errores_constancia or errores_regimen or errores_monotributo):
            raise ArcaError("Respuesta de getPersona_v2 sin datos generales ni errores")

    id_persona = (
        _entero(getattr(generales, "idPersona", None))
        or _entero(getattr(error_constancia, "idPersona", None))
        or _entero(cuit)
    )
    if not id_persona:
        raise ArcaError("Respuesta de getPersona_v2 sin idPersona")

    monotributo = getattr(persona_return, "datosMonotributo", None)
    return Constancia(
        cuit=str(id_persona).zfill(11),
        tipo_persona=_tipo_persona(getattr(generales, "tipoPersona", None)),
        tipo_clave=_texto(getattr(generales, "tipoClave", None)),
        estado_clave=_texto(getattr(generales, "estadoClave", None)),
        nombre=_texto(getattr(generales, "nombre", None))
        or _texto(getattr(error_constancia, "nombre", None)),
        apellido=_texto(getattr(generales, "apellido", None))
        or _texto(getattr(error_constancia, "apellido", None)),
        razon_social=_texto(getattr(generales, "razonSocial", None)),
        domicilio_fiscal=_domicilio(getattr(generales, "domicilioFiscal", None)),
        sucesion=_si_no(getattr(generales, "esSucesion", None)),
        fecha_fallecimiento=_fecha(getattr(generales, "fechaFallecimiento", None)),
        impuestos=_impuestos(getattr(persona_return, "datosRegimenGeneral", None)),
        impuestos_monotributo=_impuestos(monotributo),
        categoria_monotributo=_categoria(getattr(monotributo, "categoriaMonotributo", None)),
        errores_constancia=errores_constancia,
        errores_regimen_general=errores_regimen,
        errores_monotributo=errores_monotributo,
    )


def _error_de_fault(fault: Fault) -> WSPCIError:
    mensaje = _texto(fault.message) or "SOAP Fault sin detalle"
    if _PERSONA_INEXISTENTE in _clave(mensaje):
        return PersonaNoEncontrada([mensaje])
    return WSPCIError([mensaje])


def _vigente(impuesto: Impuesto) -> bool:
    return impuesto.estado in ("ACTIVO", None)


def _tiene_datos_generales(generales) -> bool:
    campos = (
        "idPersona",
        "apellido",
        "nombre",
        "razonSocial",
        "tipoPersona",
        "tipoClave",
        "estadoClave",
        "domicilioFiscal",
    )
    return any(getattr(generales, campo, None) for campo in campos)


def _errores(error) -> list[str]:
    """Mensajes de un ``error*``. Un error sin texto también cuenta: invalida la sección."""
    if error is None:
        return []
    mensajes = [_texto(e) for e in _lista(error, "error")]
    mensajes.append(_texto(getattr(error, "mensaje", None)))
    return list(dict.fromkeys(m for m in mensajes if m)) or [_ERROR_SIN_DETALLE]


def _impuestos(datos) -> list[Impuesto]:
    return [
        Impuesto(
            id=id_impuesto,
            descripcion=_texto(getattr(i, "descripcionImpuesto", None)),
            estado=_texto(getattr(i, "estadoImpuesto", None)),
            periodo=_entero(getattr(i, "periodo", None)),
        )
        for i in _lista(datos, "impuesto")
        if (id_impuesto := _entero(getattr(i, "idImpuesto", None))) is not None
    ]


def _categoria(categoria) -> Categoria | None:
    id_categoria = _entero(getattr(categoria, "idCategoria", None))
    if id_categoria is None:
        return None
    return Categoria(
        id=id_categoria,
        descripcion=_texto(getattr(categoria, "descripcionCategoria", None)),
        periodo=_entero(getattr(categoria, "periodo", None)),
    )


def _domicilio(domicilio) -> Domicilio | None:
    resultado = Domicilio(
        direccion=_texto(getattr(domicilio, "direccion", None)),
        localidad=_texto(getattr(domicilio, "localidad", None)),
        provincia=_texto(getattr(domicilio, "descripcionProvincia", None)),
        codigo_postal=_texto(getattr(domicilio, "codPostal", None)),
        dato_adicional=_texto(getattr(domicilio, "datoAdicional", None)),
    )
    return resultado if str(resultado) else None


def _tipo_persona(valor) -> TipoPersona | None:
    try:
        return TipoPersona(_texto(valor))
    except ValueError:
        return None


def _si_no(valor) -> bool:
    if isinstance(valor, bool):
        return valor
    return (_texto(valor) or "").upper() in ("SI", "SÍ", "S")


def _fecha(valor) -> datetime | None:
    if isinstance(valor, datetime):
        return valor
    if isinstance(valor, date):
        return datetime(valor.year, valor.month, valor.day)
    try:
        return datetime.fromisoformat(_texto(valor) or "")
    except ValueError:
        return None


def _lista(objeto, campo: str) -> list:
    valor = getattr(objeto, campo, None)
    if valor is None:
        return []
    # Los elementos son `nillable` en el WSDL: la lista puede traer None.
    return [v for v in valor if v is not None] if isinstance(valor, list) else [valor]


def _entero(valor) -> int | None:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _texto(valor) -> str | None:
    if valor is None:
        return None
    return " ".join(str(valor).split()) or None


def _clave(mensaje: str) -> str:
    return " ".join(mensaje.split()).casefold()
