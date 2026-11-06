"""Pruebas de WSPCI contra homologación. Solo consultan: no emiten nada.

Se saltean si faltan ``ARCA_TEST_CERT``, ``ARCA_TEST_KEY`` y ``ARCA_TEST_CUIT`` (la CUIT que
consulta). El certificado tiene que estar autorizado a ``ws_sr_constancia_inscripcion``; si no,
ARCA responde ``coe.notAuthorized``.

Los datos reales no se guardan en el repositorio. Las CUIT a consultar se pasan por entorno:

- ``ARCA_TEST_KEY_PASSWORD``: contraseña de la clave, si tiene.
- ``ARCA_TEST_CUITS``: CUIT a consultar, separadas por comas. Sin ella, ``test_constancia`` se
  saltea: la CUIT del certificado no tiene por qué estar en el padrón de homologación.
- ``ARCA_TEST_CUIL``: una CUIL.
- ``ARCA_TEST_CUIT_MONOTRIBUTO_SOCIAL``: un monotributista social.

Para responder las preguntas pendientes, correr con ``-s``: se imprime la forma de cada respuesta
(códigos, estados y mensajes de ARCA), nunca nombres ni domicilios::

    uv run pytest tests/integration -s

Esto no responde qué nombre muestra el Administrador de Relaciones para el servicio: hay que
mirarlo con clave fiscal.
"""

import os

import pytest
from zeep.exceptions import Fault

from arca_client import WSAA, WSPCI, Constancia, Environment, PersonaNoEncontrada

pytestmark = pytest.mark.skipif(
    not all(os.environ.get(v) for v in ("ARCA_TEST_CERT", "ARCA_TEST_KEY", "ARCA_TEST_CUIT")),
    reason="Faltan ARCA_TEST_CERT, ARCA_TEST_KEY o ARCA_TEST_CUIT",
)

# Dígito verificador válido y sin persona en homologación: ahí responde con un SOAP Fault.
# Otras CUIT que parecen ficticias existen: revisar antes de elegir una.
CUIT_INEXISTENTE = 30712345671


@pytest.fixture(scope="module")
def wspci():
    password = os.environ.get("ARCA_TEST_KEY_PASSWORD")
    # Con el caché en disco por defecto: un TA perdido bloquea el servicio hasta 12 h.
    wsaa = WSAA(
        os.environ["ARCA_TEST_CERT"],
        os.environ["ARCA_TEST_KEY"],
        Environment.HOMOLOGACION,
        key_password=password.encode() if password else None,
    )
    return WSPCI(wsaa, cuit=os.environ["ARCA_TEST_CUIT"])


def _forma(c: Constancia) -> dict:
    """Lo que hace falta para responder las preguntas, sin datos personales."""
    return {
        "tipo_persona": c.tipo_persona,
        "tipo_clave": c.tipo_clave,
        "estado_clave": c.estado_clave,
        "impuestos": [(i.id, i.descripcion, i.estado) for i in c.impuestos],
        "impuestos_monotributo": [(i.id, i.descripcion, i.estado) for i in c.impuestos_monotributo],
        "categoria": c.categoria_monotributo and c.categoria_monotributo.descripcion,
        "errores_constancia": c.errores_constancia,
        "errores_regimen_general": c.errores_regimen_general,
        "errores_monotributo": c.errores_monotributo,
        "condicion_iva": c.condicion_iva,
    }


def test_dummy(wspci):
    assert wspci.dummy() == {"appserver": "OK", "authserver": "OK", "dbserver": "OK"}


@pytest.mark.parametrize("cuit", [c for c in os.environ.get("ARCA_TEST_CUITS", "").split(",") if c])
def test_constancia(wspci, cuit):
    """Preguntas 2 y 3: valores de ``estadoImpuesto`` y si el impuesto 32 es IVA exento."""
    constancia = wspci.get_constancia(cuit)
    assert constancia.cuit == cuit.replace("-", "")
    print(f"\nConstancia: {_forma(constancia)}")


def test_cuit_inexistente(wspci):
    """Pregunta 1: en homologación es un SOAP Fault. La librería cubre las dos formas."""
    try:
        respuesta = wspci._service().getPersona_v2(**wspci._auth(), idPersona=CUIT_INEXISTENTE)
    except Fault as fault:
        print(f"\nCUIT inexistente: SOAP Fault: {fault.message}")
    else:
        error = getattr(respuesta, "errorConstancia", None)
        print(f"\nCUIT inexistente: errorConstancia: {getattr(error, 'error', None)}")
    with pytest.raises(PersonaNoEncontrada):
        wspci.get_constancia(CUIT_INEXISTENTE)


@pytest.mark.parametrize(
    ("variable", "pregunta"),
    [("ARCA_TEST_CUIL", "CUIL (5)"), ("ARCA_TEST_CUIT_MONOTRIBUTO_SOCIAL", "Social (4)")],
)
def test_casos_opcionales(wspci, variable, pregunta):
    """Preguntas 4 y 5: una CUIL y un monotributista social."""
    cuit = os.environ.get(variable)
    if not cuit:
        pytest.skip(f"Falta {variable}")
    print(f"\n{pregunta}: {_forma(wspci.get_constancia(cuit))}")
