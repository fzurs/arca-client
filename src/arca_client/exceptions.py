class ArcaError(Exception):
    """Error base de arca-client."""


class WSAAError(ArcaError):
    """Error devuelto por el WSAA como SoapFault.

    `code` es el código de la especificación (ej. ``coe.alreadyAuthenticated``,
    ``cms.cert.expired``).
    """

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")

    @property
    def retryable(self) -> bool:
        """Según la spec, solo `wsaa.*` y `wsn.unavailable` admiten reintento (tras 60 s)."""
        return self.code.startswith("wsaa.") or self.code == "wsn.unavailable"
