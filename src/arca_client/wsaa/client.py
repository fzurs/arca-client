"""Cliente del Web Service de Autenticación y Autorización (WSAA)."""

import os
from datetime import datetime, timezone

import zeep
from cryptography.hazmat.primitives import hashes
from zeep.exceptions import Fault

from ..environments import Environment, wsaa_wsdl
from ..exceptions import ArcaError, WSAAError
from ..transport import make_transport
from .cache import FileTicketCache, TicketCache
from .signer import load_certificate, load_private_key, sign_tra
from .ticket import AccessTicket
from .tra import build_tra


def _read(value: bytes | str | os.PathLike) -> bytes:
    """Acepta el contenido PEM en bytes o una ruta a archivo."""
    if isinstance(value, bytes):
        return value
    with open(value, "rb") as f:
        return f.read()


class WSAA:
    """Obtiene y cachea Tickets de Acceso del WSAA.

    >>> wsaa = WSAA("cert.crt", "private.key", Environment.HOMOLOGACION)
    >>> ticket = wsaa.get_ticket("wsfe")
    >>> ticket.token, ticket.sign
    """

    def __init__(
        self,
        cert: bytes | str | os.PathLike,
        key: bytes | str | os.PathLike,
        env: Environment = Environment.HOMOLOGACION,
        *,
        key_password: bytes | None = None,
        cache: TicketCache | None = None,
        hash_algorithm: hashes.HashAlgorithm | None = None,
        timeout: float = 30,
    ) -> None:
        self.env = Environment(env)
        self.certificate = load_certificate(_read(cert))
        self.private_key = load_private_key(_read(key), key_password)
        self.cache = cache if cache is not None else FileTicketCache()
        self.hash_algorithm = hash_algorithm
        self.timeout = timeout
        self._soap: zeep.Client | None = None

    def get_ticket(self, service: str, *, force: bool = False) -> AccessTicket:
        """Devuelve un TA vigente para `service`, del caché o pidiéndolo al WSAA."""
        key = self._cache_key(service)
        if not force:
            cached = self.cache.get(key)
            if cached is not None and cached.is_valid():
                return cached

        ticket = self.login(service)
        self.cache.set(key, ticket)
        return ticket

    def login(self, service: str) -> AccessTicket:
        """Pide un TA nuevo al WSAA, sin pasar por el caché."""
        tra = build_tra(service, now=datetime.now(timezone.utc))
        cms = sign_tra(tra, self.certificate, self.private_key, self.hash_algorithm)
        try:
            response = self._login_cms(cms)
        except Fault as fault:
            error = WSAAError(_fault_code(fault), fault.message or "")
            if error.code == "coe.alreadyAuthenticated":
                raise WSAAError(
                    error.code,
                    f"Ya existe un TA vigente para '{service}' que no está en el caché. "
                    "No se puede recuperar: hay que esperar a que expire (hasta 12 h). "
                    f"Detalle: {error.message}",
                ) from fault
            raise error from fault
        if not response:
            raise ArcaError("El WSAA devolvió una respuesta vacía")
        return AccessTicket.from_xml(response)

    def _login_cms(self, cms: str) -> str:
        return self._client().service.loginCms(in0=cms)

    def _client(self) -> zeep.Client:
        if self._soap is None:
            transport = make_transport(self.env, self.timeout)
            self._soap = zeep.Client(wsaa_wsdl(self.env), transport=transport)
        return self._soap

    def _cache_key(self, service: str) -> str:
        fingerprint = self.certificate.fingerprint(hashes.SHA256()).hex()[:16]
        return f"{self.env.value}-{service}-{fingerprint}"


def _fault_code(fault: Fault) -> str:
    # El WSAA (Axis) devuelve códigos como "ns1:coe.alreadyAuthenticated".
    code = fault.code or ""
    if hasattr(code, "localname"):
        code = code.localname
    return str(code).rsplit(":", 1)[-1]
