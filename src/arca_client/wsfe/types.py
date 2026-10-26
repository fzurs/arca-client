"""Códigos y estructuras de WSFEv1 (Manual del desarrollador, RG 4291)."""

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from enum import IntEnum

_CENTS = Decimal("0.01")


class CbteTipo(IntEnum):
    """Tipos de comprobante soportados: clase A y B (responsables inscriptos) y C (monotributo)."""

    FACTURA_A = 1
    NOTA_DEBITO_A = 2
    NOTA_CREDITO_A = 3
    FACTURA_B = 6
    FACTURA_C = 11

    @property
    def letra(self) -> str:
        return _LETRAS[self]

    @property
    def nombre(self) -> str:
        """``"Factura"``, ``"Nota de Crédito"`` o ``"Nota de Débito"``."""
        return _NOMBRES.get(self, "Factura")

    @property
    def descripcion(self) -> str:
        """Ej. ``"Factura A"``, ``"Nota de Crédito A"``."""
        return f"{self.nombre} {self.letra}"


_LETRAS = {
    CbteTipo.FACTURA_A: "A",
    CbteTipo.NOTA_DEBITO_A: "A",
    CbteTipo.NOTA_CREDITO_A: "A",
    CbteTipo.FACTURA_B: "B",
    CbteTipo.FACTURA_C: "C",
}
_NOMBRES = {
    CbteTipo.NOTA_DEBITO_A: "Nota de Débito",
    CbteTipo.NOTA_CREDITO_A: "Nota de Crédito",
}


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
    """Condición frente al IVA del receptor (RG 5616).

    Todas son válidas para clase C; para A y B ver ``WSFE.condiciones_iva_receptor``.
    """

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


class Alicuota(IntEnum):
    """Alícuotas de IVA con su Id de ARCA (``FEParamGetTiposIva``)."""

    IVA_0 = 3
    IVA_10_5 = 4
    IVA_21 = 5
    IVA_27 = 6
    IVA_5 = 8
    IVA_2_5 = 9

    @property
    def porcentaje(self) -> Decimal:
        """Ej. ``Decimal("10.5")``."""
        return _PORCENTAJES[self]


_PORCENTAJES = {
    Alicuota.IVA_0: Decimal("0"),
    Alicuota.IVA_10_5: Decimal("10.5"),
    Alicuota.IVA_21: Decimal("21"),
    Alicuota.IVA_27: Decimal("27"),
    Alicuota.IVA_5: Decimal("5"),
    Alicuota.IVA_2_5: Decimal("2.5"),
}


class TipoTributo(IntEnum):
    """Tipos de tributo con su Id de ARCA (``FEParamGetTiposTributos``)."""

    IMPUESTOS_NACIONALES = 1
    IMPUESTOS_PROVINCIALES = 2
    IMPUESTOS_MUNICIPALES = 3
    IMPUESTOS_INTERNOS = 4
    INGRESOS_BRUTOS = 5
    PERCEPCION_IVA = 6
    PERCEPCION_IIBB = 7
    PERCEPCION_IMPUESTOS_MUNICIPALES = 8
    OTRAS_PERCEPCIONES = 9
    PERCEPCION_IVA_NO_CATEGORIZADO = 13
    OTROS = 99


TRIBUTO_TEXTO = {
    TipoTributo.IMPUESTOS_NACIONALES: "Impuestos nacionales",
    TipoTributo.IMPUESTOS_PROVINCIALES: "Impuestos provinciales",
    TipoTributo.IMPUESTOS_MUNICIPALES: "Impuestos municipales",
    TipoTributo.IMPUESTOS_INTERNOS: "Impuestos internos",
    TipoTributo.INGRESOS_BRUTOS: "Ingresos Brutos",
    TipoTributo.PERCEPCION_IVA: "Percepción de IVA",
    TipoTributo.PERCEPCION_IIBB: "Percepción de Ingresos Brutos",
    TipoTributo.PERCEPCION_IMPUESTOS_MUNICIPALES: "Percepción de impuestos municipales",
    TipoTributo.OTRAS_PERCEPCIONES: "Otras percepciones",
    TipoTributo.PERCEPCION_IVA_NO_CATEGORIZADO: "Percepción de IVA a no categorizado",
    TipoTributo.OTROS: "Otros",
}


@dataclass(frozen=True)
class Mensaje:
    """Error, observación o evento devuelto por WSFE."""

    code: int
    msg: str

    def __str__(self) -> str:
        return f"{self.code}: {self.msg}"


@dataclass(frozen=True)
class IvaAlicuota:
    """IVA de una alícuota: base imponible (neto gravado) e importe."""

    alicuota: Alicuota
    base_imponible: Decimal
    importe: Decimal


@dataclass(frozen=True)
class Tributo:
    """Otro tributo del comprobante (percepciones, impuestos internos, etc.).

    ``alicuota`` es un porcentaje (ej. ``"3.5"``). Si no se indica ``importe``, se calcula
    como ``base_imponible * alicuota / 100`` con redondeo half even. Sin ``descripcion``
    se usa el nombre del tipo.
    """

    tipo: TipoTributo | int
    base_imponible: Decimal
    alicuota: Decimal
    importe: Decimal | None = None
    descripcion: str = ""

    def __post_init__(self) -> None:
        base = _monto(self.base_imponible, "La base imponible del tributo")
        alicuota = _decimal(self.alicuota, "La alícuota del tributo")
        if alicuota < 0:
            raise ValueError("La alícuota del tributo no puede ser negativa")
        if self.importe is None:
            importe = (base * alicuota / 100).quantize(_CENTS, rounding=ROUND_HALF_EVEN)
        else:
            importe = _monto(self.importe, "El importe del tributo")
        if importe <= 0:
            raise ValueError("El importe del tributo debe ser mayor a 0")
        object.__setattr__(self, "base_imponible", base)
        object.__setattr__(self, "alicuota", alicuota)
        object.__setattr__(self, "importe", importe)
        if not self.descripcion:
            object.__setattr__(self, "descripcion", TRIBUTO_TEXTO.get(self.tipo, "Tributo"))


@dataclass(frozen=True)
class ComprobanteAsociado:
    """Comprobante al que se refiere una nota de crédito o débito (``CbtesAsoc``)."""

    tipo: CbteTipo | int
    punto_venta: int
    numero: int
    cuit: int | None = None  # emisor del comprobante asociado
    fecha: date | None = None

    @classmethod
    def from_comprobante(cls, comprobante: "ComprobanteAutorizado") -> "ComprobanteAsociado":
        return cls(
            tipo=comprobante.tipo,
            punto_venta=comprobante.punto_venta,
            numero=comprobante.numero,
            cuit=comprobante.cuit,
            fecha=comprobante.fecha,
        )


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
    # Composición del total: neto + no gravado + exento + IVA + tributos.
    importe_neto: Decimal = Decimal(0)
    importe_iva: Decimal = Decimal(0)
    importe_no_gravado: Decimal = Decimal(0)
    importe_exento: Decimal = Decimal(0)
    importe_tributos: Decimal = Decimal(0)
    iva: list[IvaAlicuota] = field(default_factory=list)
    tributos: list[Tributo] = field(default_factory=list)
    # Solo notas de crédito/débito: comprobantes o período al que se refieren.
    asociados: list[ComprobanteAsociado] = field(default_factory=list)
    periodo_asociado: tuple[date, date] | None = None


def _decimal(value, nombre: str) -> Decimal:
    if isinstance(value, float):
        raise TypeError(f"{nombre}: usá Decimal, int o str, no float")
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError) as e:
        raise ValueError(f"{nombre} es inválido: {value!r}") from e


def _monto(value, nombre: str) -> Decimal:
    """Importe en pesos: no negativo y con hasta 2 decimales."""
    amount = _decimal(value, nombre)
    if amount < 0:
        raise ValueError(f"{nombre} no puede ser negativo")
    if amount != amount.quantize(_CENTS, rounding=ROUND_HALF_EVEN):
        raise ValueError(f"{nombre} admite como máximo 2 decimales")
    return amount.quantize(_CENTS)
