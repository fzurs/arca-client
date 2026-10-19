"""Representación impresa (PDF) de una Factura C con su código QR.

Requiere el extra ``pdf``: ``pip install arca-client[pdf]``.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from io import BytesIO

from .qr import qr_url
from .types import ComprobanteAutorizado, Concepto, CondicionIVAReceptor, DocTipo

_CENTS = Decimal("0.01")
LEYENDA_CONSUMIDOR_FINAL = "A CONSUMIDOR FINAL"

CONDICION_IVA_TEXTO = {
    CondicionIVAReceptor.IVA_RESPONSABLE_INSCRIPTO: "IVA Responsable Inscripto",
    CondicionIVAReceptor.IVA_SUJETO_EXENTO: "IVA Sujeto Exento",
    CondicionIVAReceptor.CONSUMIDOR_FINAL: "Consumidor Final",
    CondicionIVAReceptor.RESPONSABLE_MONOTRIBUTO: "Responsable Monotributo",
    CondicionIVAReceptor.SUJETO_NO_CATEGORIZADO: "Sujeto No Categorizado",
    CondicionIVAReceptor.PROVEEDOR_DEL_EXTERIOR: "Proveedor del Exterior",
    CondicionIVAReceptor.CLIENTE_DEL_EXTERIOR: "Cliente del Exterior",
    CondicionIVAReceptor.IVA_LIBERADO_LEY_19640: "IVA Liberado - Ley N° 19.640",
    CondicionIVAReceptor.MONOTRIBUTISTA_SOCIAL: "Monotributista Social",
    CondicionIVAReceptor.IVA_NO_ALCANZADO: "IVA No Alcanzado",
    CondicionIVAReceptor.MONOTRIBUTO_TRABAJADOR_INDEPENDIENTE_PROMOVIDO: (
        "Monotributo Trabajador Independiente Promovido"
    ),
}


@dataclass(frozen=True)
class Emisor:
    razon_social: str
    domicilio: str
    inicio_actividades: date
    ingresos_brutos: str  # número de inscripción, "Exento", "Convenio Multilateral", etc.
    condicion_iva: str = "Responsable Monotributo"


@dataclass(frozen=True)
class Receptor:
    """Datos del receptor que no viajan a ARCA. El documento y la condición frente
    al IVA se toman del comprobante autorizado."""

    nombre: str = ""
    domicilio: str = ""


@dataclass(frozen=True)
class Item:
    descripcion: str
    precio_unitario: Decimal
    cantidad: Decimal = Decimal(1)
    unidad: str = "unidades"
    codigo: str = ""
    bonificacion: Decimal = Decimal(0)  # porcentaje de descuento, de 0 a 100

    def __post_init__(self) -> None:
        object.__setattr__(self, "precio_unitario", _decimal(self.precio_unitario, "precio"))
        object.__setattr__(self, "cantidad", _decimal(self.cantidad, "cantidad"))
        object.__setattr__(self, "bonificacion", _decimal(self.bonificacion, "bonificación"))
        if self.cantidad <= 0:
            raise ValueError("La cantidad debe ser mayor a 0")
        if self.precio_unitario < 0:
            raise ValueError("El precio unitario no puede ser negativo")
        if not 0 <= self.bonificacion <= 100:
            raise ValueError("La bonificación es un porcentaje entre 0 y 100")

    @property
    def importe_bruto(self) -> Decimal:
        return _round(self.cantidad * self.precio_unitario)

    @property
    def importe_bonificacion(self) -> Decimal:
        return _round(self.importe_bruto * self.bonificacion / 100)

    @property
    def subtotal(self) -> Decimal:
        """Importe del ítem con la bonificación aplicada."""
        return self.importe_bruto - self.importe_bonificacion


def render_invoice_pdf(
    factura: ComprobanteAutorizado,
    emisor: Emisor,
    items: list[Item],
    receptor: Receptor | None = None,
    *,
    condicion_venta: str = "Contado",
    copias: tuple[str, ...] = ("ORIGINAL",),
) -> bytes:
    """Genera el PDF de la Factura C y lo devuelve como bytes.

    La suma de los subtotales de `items` tiene que coincidir con el importe
    autorizado por ARCA; si no, se lanza ``ValueError``.
    """
    if not items:
        raise ValueError("La factura necesita al menos un ítem")
    total_items = sum((i.subtotal for i in items), Decimal(0))
    if total_items != factura.importe_total:
        raise ValueError(
            f"La suma de los ítems ({total_items}) no coincide con el importe autorizado "
            f"({factura.importe_total})"
        )
    try:
        import segno
        from fpdf import FPDF
    except ImportError as e:  # pragma: no cover
        raise ImportError("Para generar PDFs instalá el extra: pip install arca-client[pdf]") from e

    receptor = receptor or Receptor()
    qr_png = BytesIO()
    segno.make(qr_url(factura), error="m").save(qr_png, kind="png", scale=8, border=0)

    pdf = FPDF(format="A4", unit="mm")
    pdf.set_title(_txt(f"Factura C {_numero(factura)}"))
    pdf.set_author(_txt(emisor.razon_social))
    pdf.set_auto_page_break(True, margin=15)
    pdf.set_margins(10, 10, 10)
    rangos = []
    for copia in copias:
        pdf.add_page()
        primera = pdf.page
        _encabezado(pdf, factura, emisor, copia)
        _periodo(pdf, factura)
        _receptor(pdf, factura, receptor, condicion_venta)
        _items(pdf, items)
        _pie(pdf, factura, qr_png)
        rangos.append((copia, primera, pdf.page))
    _numerar_paginas(pdf, factura, rangos)
    return bytes(pdf.output())


def _numerar_paginas(pdf, factura: ComprobanteAutorizado, rangos) -> None:
    """Pie en cada página para identificar las hojas de continuación."""
    ultima = pdf.page
    pdf.set_auto_page_break(False)
    pdf.set_font("Helvetica", "", 7)
    for copia, primera, final in rangos:
        total = final - primera + 1
        for n, page in enumerate(range(primera, final + 1), 1):
            pdf.page = page
            pdf.set_xy(pdf.l_margin, pdf.h - 10)
            texto = f"Factura C {_numero(factura)} - {copia} - Pág. {n}/{total}"
            pdf.cell(_W, 4, _txt(texto), align="R")
    pdf.page = ultima


# --- Secciones ---------------------------------------------------------------

_W = 190  # ancho útil


def _encabezado(pdf, factura: ComprobanteAutorizado, emisor: Emisor, copia: str) -> None:
    x0 = pdf.l_margin
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(_W, 8, _txt(copia), border=1, align="C", new_x="LMARGIN", new_y="NEXT")

    top, height, half = pdf.get_y(), 48, _W / 2
    pdf.rect(x0, top, _W, height)
    pdf.line(x0 + half, top + 16, x0 + half, top + height)

    # Letra del comprobante
    box = 16
    pdf.set_fill_color(255, 255, 255)
    pdf.rect(x0 + half - box / 2, top, box, box, style="DF")
    pdf.set_xy(x0 + half - box / 2, top + 1)
    pdf.set_font("Helvetica", "B", 24)
    pdf.cell(box, 10, "C", align="C")
    pdf.set_xy(x0 + half - box / 2, top + 10)
    pdf.set_font("Helvetica", "B", 7)
    pdf.cell(box, 5, f"COD. {int(factura.tipo):03d}", align="C")

    # Emisor (izquierda)
    pdf.set_xy(x0 + 3, top + 4)
    pdf.set_font("Helvetica", "B", 15)
    pdf.multi_cell(half - box / 2 - 6, 7, _txt(emisor.razon_social), align="C")
    y = max(pdf.get_y() + 2, top + 20)
    for label, value in (
        ("Razón Social", emisor.razon_social),
        ("Domicilio Comercial", emisor.domicilio),
        ("Condición frente al IVA", emisor.condicion_iva),
    ):
        pdf.set_xy(x0 + 3, y)
        _label_value(pdf, label, value, half - 6)
        y = pdf.get_y() + 1

    # Comprobante (derecha)
    xr = x0 + half + 4
    pdf.set_xy(xr + box / 2, top + 3)
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(half - box / 2 - 6, 10, "FACTURA")
    pdf.set_xy(xr, top + 18)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(
        half - 6,
        5,
        f"Punto de Venta: {factura.punto_venta:05d}     Comp. Nro: {factura.numero:08d}",
    )
    y = top + 25
    for label, value in (
        ("Fecha de Emisión", _fecha(factura.fecha)),
        ("CUIT", _cuit(factura.cuit)),
        ("Ingresos Brutos", emisor.ingresos_brutos),
        ("Fecha de Inicio de Actividades", _fecha(emisor.inicio_actividades)),
    ):
        pdf.set_xy(xr, y)
        _label_value(pdf, label, value, half - 8)
        y = pdf.get_y() + 0.5
    pdf.set_xy(x0, top + height + 1)


def _periodo(pdf, factura: ComprobanteAutorizado) -> None:
    if factura.concepto is Concepto.PRODUCTOS:
        return
    top = pdf.get_y()
    pdf.rect(pdf.l_margin, top, _W, 8)
    pdf.set_xy(pdf.l_margin + 3, top + 1.5)
    for label, value, width in (
        ("Período Facturado Desde", _fecha(factura.servicio_desde), 62),
        ("Hasta", _fecha(factura.servicio_hasta), 32),
        ("Fecha de Vto. para el pago", _fecha(factura.vencimiento_pago), 90),
    ):
        _label_value(pdf, label, value, width, inline=True)
    pdf.set_xy(pdf.l_margin, top + 9)


def _receptor(pdf, factura: ComprobanteAutorizado, receptor: Receptor, condicion_venta) -> None:
    top = pdf.get_y()
    x0, half = pdf.l_margin, _W / 2
    pdf.set_xy(x0 + 3, top + 2)
    if factura.condicion_iva_receptor is CondicionIVAReceptor.CONSUMIDOR_FINAL:
        # Leyenda exigida por la RG 1415 (Anexo II) para receptores consumidores finales.
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(half - 6, 5, LEYENDA_CONSUMIDOR_FINAL, new_x="LEFT", new_y="NEXT")
        pdf.set_xy(x0 + 3, pdf.get_y() + 1)
    if factura.doc_tipo is not DocTipo.CONSUMIDOR_FINAL:
        if factura.doc_tipo in (DocTipo.CUIT, DocTipo.CUIL):
            doc_value = _cuit(factura.doc_nro)
        else:
            doc_value = str(factura.doc_nro)
        _label_value(pdf, factura.doc_tipo.name, doc_value, half - 6)
        pdf.set_xy(x0 + 3, pdf.get_y() + 1)
    condicion = CONDICION_IVA_TEXTO.get(factura.condicion_iva_receptor, "-")
    _label_value(pdf, "Condición frente al IVA", condicion, half - 6)
    pdf.set_xy(x0 + 3, pdf.get_y() + 1)
    _label_value(pdf, "Condición de venta", condicion_venta, half - 6)
    y_left = pdf.get_y()

    pdf.set_xy(x0 + half, top + 2)
    _label_value(pdf, "Apellido y Nombre / Razón Social", receptor.nombre or "-", half - 3)
    pdf.set_xy(x0 + half, pdf.get_y() + 1)
    _label_value(pdf, "Domicilio", receptor.domicilio or "-", half - 3)
    bottom = max(y_left, pdf.get_y()) + 2
    pdf.rect(x0, top, _W, bottom - top)
    pdf.set_xy(x0, bottom + 2)


def _items(pdf, items: list[Item]) -> None:
    # Las columnas de bonificación solo se muestran si algún ítem tiene descuento.
    con_bonif = any(item.bonificacion for item in items)
    if con_bonif:
        widths = (16, 58, 17, 17, 22, 16, 20, 24)
        align = ("LEFT", "LEFT", "RIGHT", "CENTER", "RIGHT", "RIGHT", "RIGHT", "RIGHT")
        header = (
            "Código",
            "Producto / Servicio",
            "Cantidad",
            "U. Medida",
            "Precio Unit.",
            "% Bonif",
            "Imp. Bonif.",
            "Subtotal",
        )
    else:
        widths = (18, 80, 20, 20, 26, 26)
        align = ("LEFT", "LEFT", "RIGHT", "CENTER", "RIGHT", "RIGHT")
        header = (
            "Código",
            "Producto / Servicio",
            "Cantidad",
            "U. Medida",
            "Precio Unit.",
            "Subtotal",
        )

    pdf.set_font("Helvetica", "", 8)
    pdf.set_draw_color(150, 150, 150)
    with pdf.table(
        col_widths=widths,
        text_align=align,
        line_height=5,
        headings_style=_headings_style(),
        first_row_as_headings=True,
        repeat_headings=1,
    ) as table:
        table.row(header)
        for item in items:
            row = [
                _txt(item.codigo),
                _txt(item.descripcion),
                _qty(item.cantidad),
                _txt(item.unidad),
                _money(item.precio_unitario),
            ]
            if con_bonif:
                row += [_money(item.bonificacion), _money(item.importe_bonificacion)]
            row.append(_money(item.subtotal))
            table.row(row)
    pdf.set_draw_color(0, 0, 0)


def _pie(pdf, factura: ComprobanteAutorizado, qr_png: BytesIO) -> None:
    needed = 72
    if pdf.get_y() + needed > pdf.h - pdf.b_margin:
        pdf.add_page()
    x0 = pdf.l_margin
    top = pdf.h - pdf.b_margin - needed

    # Totales
    pdf.rect(x0, top, _W, 26)
    y = top + 3
    for label, value, bold in (
        ("Subtotal: $", factura.importe_total, False),
        ("Importe Otros Tributos: $", Decimal(0), False),
        ("Importe Total: $", factura.importe_total, True),
    ):
        pdf.set_xy(x0 + 90, y)
        pdf.set_font("Helvetica", "B", 10 if bold else 9)
        pdf.cell(65, 6, _txt(label), align="R")
        pdf.cell(32, 6, _money(value), align="R")
        y += 7

    # QR y CAE
    qr_size = 36
    qr_top = top + 32
    qr_png.seek(0)
    pdf.image(qr_png, x=x0, y=qr_top, w=qr_size, h=qr_size)
    pdf.set_xy(x0 + qr_size + 6, qr_top + 4)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(80, 6, "Comprobante Autorizado")
    pdf.set_xy(x0 + 120, qr_top + 4)
    _label_value(pdf, "CAE N°", factura.cae, 70, inline=True)
    pdf.set_xy(x0 + 120, qr_top + 10)
    _label_value(pdf, "Fecha de Vto. de CAE", _fecha(factura.cae_vencimiento), 70, inline=True)


# --- Helpers -----------------------------------------------------------------


def _headings_style():
    from fpdf.fonts import FontFace

    return FontFace(emphasis="BOLD", fill_color=(225, 225, 225))


def _label_value(pdf, label: str, value: str, width: float, inline: bool = False) -> None:
    pdf.set_font("Helvetica", "B", 8.5)
    label_text = _txt(f"{label}: ")
    label_w = pdf.get_string_width(label_text) + 0.5
    if inline:
        pdf.cell(label_w, 5, label_text)
        pdf.set_font("Helvetica", "", 8.5)
        pdf.cell(max(width - label_w, 1), 5, _txt(value))
        return
    x = pdf.get_x()
    pdf.cell(label_w, 5, label_text)
    pdf.set_font("Helvetica", "", 8.5)
    pdf.multi_cell(max(width - label_w, 10), 5, _txt(value), new_x="LEFT", new_y="NEXT")
    pdf.set_x(x)


_REPLACEMENTS = str.maketrans(
    {"–": "-", "—": "-", "“": '"', "”": '"', "‘": "'", "’": "'", "…": "..."}
)


def _txt(value) -> str:
    """Las fuentes estándar de PDF solo cubren Latin-1 (incluye á, é, ñ, ü, °)."""
    text = str(value).translate(_REPLACEMENTS)
    return text.encode("latin-1", "replace").decode("latin-1")


def _round(value: Decimal) -> Decimal:
    return value.quantize(_CENTS, rounding=ROUND_HALF_EVEN)


def _money(value: Decimal) -> str:
    text = f"{value.quantize(_CENTS):,.2f}"
    return text.replace(",", "_").replace(".", ",").replace("_", ".")


def _qty(value: Decimal) -> str:
    text = f"{value.normalize():f}"
    return text.replace(".", ",")


def _fecha(value: date | None) -> str:
    return value.strftime("%d/%m/%Y") if value else "-"


def _cuit(value: int) -> str:
    s = f"{value:011d}"
    return f"{s[:2]}-{s[2:10]}-{s[10]}"


def _numero(factura: ComprobanteAutorizado) -> str:
    return f"{factura.punto_venta:05d}-{factura.numero:08d}"


def _decimal(value, nombre: str) -> Decimal:
    if isinstance(value, float):
        raise TypeError(f"Usá Decimal, int o str para {nombre}, no float")
    try:
        return Decimal(value)
    except InvalidOperation as e:
        raise ValueError(f"{nombre.capitalize()} inválido: {value!r}") from e
