"""Transporte HTTP compartido por los clientes SOAP."""

import ssl

import requests
from requests.adapters import HTTPAdapter
from zeep.transports import Transport

from .environments import Environment

# Servidores de producción con una clave DH chica (DH_KEY_TOO_SMALL con OpenSSL 3):
# WSFEv1 y WSCDC. El WSAA de producción no la necesita.
PRODUCCION_DH_CHICA = ("https://servicios1.afip.gov.ar/",)


class _LowSecurityAdapter(HTTPAdapter):
    """Acepta la clave DH chica que usan algunos servidores de ARCA y que OpenSSL 3
    rechaza (DH_KEY_TOO_SMALL). Se siguen verificando el certificado y el host."""

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def make_transport(env: Environment, timeout: float) -> Transport:
    session = requests.Session()
    if env is Environment.HOMOLOGACION:
        session.mount("https://", _LowSecurityAdapter())
    else:
        # Solo para esos servidores: el resto usa la configuración TLS por defecto.
        for prefix in PRODUCCION_DH_CHICA:
            session.mount(prefix, _LowSecurityAdapter())
    # `timeout` aplica a la descarga del WSDL y `operation_timeout` a cada llamada.
    return Transport(session=session, timeout=timeout, operation_timeout=timeout)
