from enum import Enum


class Environment(str, Enum):
    """Ambientes de ARCA."""

    HOMOLOGACION = "homologacion"
    PRODUCCION = "produccion"


WSAA_URLS = {
    Environment.HOMOLOGACION: "https://wsaahomo.afip.gov.ar/ws/services/LoginCms",
    Environment.PRODUCCION: "https://wsaa.afip.gov.ar/ws/services/LoginCms",
}


def wsaa_wsdl(env: Environment) -> str:
    return f"{WSAA_URLS[env]}?wsdl"
