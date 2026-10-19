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


class WSFEError(ArcaError):
    """WSFE rechazó el pedido.

    `errores` son los ``<Errors>`` generales y `observaciones` los ``<Obs>`` del
    comprobante; ambos son listas de :class:`arca_client.wsfe.Mensaje`.
    """

    def __init__(self, errores: list, observaciones: list | None = None):
        self.errores = errores
        self.observaciones = observaciones or []
        detalle = "; ".join(str(m) for m in [*self.errores, *self.observaciones])
        super().__init__(detalle or "WSFE rechazó el comprobante sin detalle")

    @property
    def codes(self) -> list[int]:
        return [m.code for m in [*self.errores, *self.observaciones]]


class WSFEConnectionError(ArcaError):
    """Se interrumpió la comunicación al pedir el CAE.

    - ``safe_to_retry=True``: se verificó con ``FECompConsultar`` que ARCA no
      registró el comprobante; se puede volver a llamar a ``create_invoice_c``.
    - ``safe_to_retry=False``: no se pudo verificar. **No reintentar** con
      ``create_invoice_c`` (emitiría otro número): consultá
      ``WSFE.get_voucher(punto_venta, tipo, numero)`` cuando vuelva la conexión.
    """

    def __init__(self, message: str, *, punto_venta: int, tipo, numero: int, safe_to_retry: bool):
        self.punto_venta = punto_venta
        self.tipo = tipo
        self.numero = numero
        self.safe_to_retry = safe_to_retry
        super().__init__(message)
