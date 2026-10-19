"""Códigos y estructuras de WSFEv1 (Manual del desarrollador, RG 4291)."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import IntEnum


class CbteTipo(IntEnum):
    """Tipos de comprobante soportados por ahora (clase C, monotributistas)."""

    FACTURA_C = 11


class Concepto(IntEnum):
    PRODUCTOS = 1
    SERVICIOS = 2
    PRODUCTOS_Y_SERVICIOS = 3


class DocTipo(IntEnum):
    CUIT = 80
    CUIL = 86
    DNI = 96
    CONSUMIDOR_FINAL = 99  # sin identificar: DocNro = 0


class CondicionIVAReceptor(IntEnum):
    """Condición frente al IVA del receptor (RG 5616). Todas son válidas para clase C."""

    IVA_RESPONSABLE_INSCRIPTO = 1
    IVA_SUJETO_EXENTO = 4
    CONSUMIDOR_FINAL = 5
    RESPONSABLE_MONOTRIBUTO = 6
    SUJETO_NO_CATEGORIZADO = 7
    PROVEEDOR_DEL_EXTERIOR = 8
    CLIENTE_DEL_EXTERIOR = 9
    IVA_LIBERADO_LEY_19640 = 10
    MONOTRIBUTISTA_SOCIAL = 13
    IVA_NO_ALCANZADO = 15
    MONOTRIBUTO_TRABAJADOR_INDEPENDIENTE_PROMOVIDO = 16


@dataclass(frozen=True)
class Mensaje:
    """Error, observación o evento devuelto por WSFE."""

    code: int
    msg: str

    def __str__(self) -> str:
        return f"{self.code}: {self.msg}"


@dataclass(frozen=True)
class ComprobanteAutorizado:
    cuit: int  # emisor
    punto_venta: int
    tipo: CbteTipo
    numero: int
    fecha: date
    importe_total: Decimal
    cae: str
    cae_vencimiento: date
    concepto: Concepto
    doc_tipo: DocTipo
    doc_nro: int
    condicion_iva_receptor: CondicionIVAReceptor | None  # None en comprobantes viejos
    servicio_desde: date | None = None
    servicio_hasta: date | None = None
    vencimiento_pago: date | None = None
    observaciones: list[Mensaje] = field(default_factory=list)
    eventos: list[Mensaje] = field(default_factory=list)
