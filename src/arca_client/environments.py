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


WSFE_URLS = {
    Environment.HOMOLOGACION: "https://wswhomo.afip.gov.ar/wsfev1/service.asmx",
    Environment.PRODUCCION: "https://servicios1.afip.gov.ar/wsfev1/service.asmx",
}


def wsfe_wsdl(env: Environment) -> str:
    return f"{WSFE_URLS[env]}?WSDL"


WSCDC_URLS = {
    Environment.HOMOLOGACION: "https://wswhomo.afip.gov.ar/WSCDC/service.asmx",
    Environment.PRODUCCION: "https://servicios1.afip.gov.ar/WSCDC/service.asmx",
}


def wscdc_wsdl(env: Environment) -> str:
    return f"{WSCDC_URLS[env]}?WSDL"


# El manual de la Constancia de Inscripción indica hosts `*.arca.gov.ar`, pero no conectaron
# en la prueba; los `*.afip.gov.ar` sí responden.
WSPCI_URLS = {
    Environment.HOMOLOGACION: "https://awshomo.afip.gov.ar/sr-padron/webservices/personaServiceA5",
    Environment.PRODUCCION: "https://aws.afip.gov.ar/sr-padron/webservices/personaServiceA5",
}


def wspci_wsdl(env: Environment) -> str:
    return f"{WSPCI_URLS[env]}?WSDL"
