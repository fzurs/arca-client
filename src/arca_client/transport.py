"""Transporte HTTP compartido por los clientes SOAP."""

import ssl

import requests
from requests.adapters import HTTPAdapter
from zeep.transports import Transport

from .environments import Environment


class _LowSecurityAdapter(HTTPAdapter):
    """Los servidores de homologación usan una clave DH chica que OpenSSL 3
    rechaza (DH_KEY_TOO_SMALL)."""

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def make_transport(env: Environment, timeout: float) -> Transport:
    session = requests.Session()
    if env is Environment.HOMOLOGACION:
        session.mount("https://", _LowSecurityAdapter())
    # `timeout` aplica a la descarga del WSDL y `operation_timeout` a cada llamada.
    return Transport(session=session, timeout=timeout, operation_timeout=timeout)
