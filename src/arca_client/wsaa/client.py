"""Cliente del Web Service de Autenticación y Autorización (WSAA)."""

import os
from contextlib import nullcontext
from datetime import datetime, timezone

import zeep
from cryptography.hazmat.primitives import hashes
from zeep.exceptions import Fault

from ..certificates import certificate_paths, read_pem
from ..environments import Environment, wsaa_wsdl
from ..exceptions import ArcaError, WSAAError
from ..transport import make_transport
from .cache import FileTicketCache, TicketCache
from .signer import load_certificate, load_private_key, sign_tra
from .ticket import AccessTicket
from .tra import build_tra


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
        self.certificate = load_certificate(read_pem(cert))
        self.private_key = load_private_key(read_pem(key), key_password)
        self.cache = cache if cache is not None else FileTicketCache()
        self.hash_algorithm = hash_algorithm
        self.timeout = timeout
        self._soap: zeep.Client | None = None

    @classmethod
    def from_alias(
        cls,
        alias: str,
        env: Environment | str = Environment.HOMOLOGACION,
        *,
        base_dir: str | os.PathLike = "certs",
        **kwargs,
    ) -> "WSAA":
        """Carga el certificado y la clave desde donde los deja `arca-setup`.

        Busca ``<base_dir>/<ambiente>/<alias>.crt`` y ``.key`` (ver
        :func:`arca_client.certificate_paths`); el resto de los argumentos van a
        ``__init__`` (`key_password`, `cache`, `hash_algorithm`, `timeout`).

        >>> wsaa = WSAA.from_alias("miapp", Environment.HOMOLOGACION)
        """
        cert_path, key_path = certificate_paths(alias, env, base_dir)
        for path in (cert_path, key_path):
            if not path.is_file():
                raise ArcaError(
                    f"No existe {path}. Generá el certificado con `arca-setup` "
                    "o revisá el alias, el ambiente y la carpeta base."
                )
        return cls(cert_path, key_path, env, **kwargs)

    def get_ticket(self, service: str, *, force: bool = False) -> AccessTicket:
        """Devuelve un TA vigente para `service`, del caché o pidiéndolo al WSAA."""
        key = self._cache_key(service)
        if not force and (cached := self._cached(key)):
            return cached

        lock = getattr(self.cache, "lock", None)  # opcional en backends propios
        with lock(key) if lock else nullcontext():
            # Mientras se esperaba el bloqueo, otro proceso pudo haber obtenido el TA.
            if not force and (cached := self._cached(key)):
                return cached
            try:
                ticket = self.login(service)
            except WSAAError as error:
                # Otro proceso con un backend sin bloqueo pudo ganarnos de mano.
                if error.code == "coe.alreadyAuthenticated" and (cached := self._cached(key)):
                    return cached
                raise
            self.cache.set(key, ticket)
            return ticket

    def _cached(self, key: str) -> AccessTicket | None:
        cached = self.cache.get(key)
        return cached if cached is not None and cached.is_valid() else None

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
