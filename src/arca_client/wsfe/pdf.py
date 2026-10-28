"""Representación impresa (PDF) de comprobantes A, B y C con su código QR.

Requiere el extra ``pdf``: ``pip install arca-client[pdf]``.
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from functools import cache
from io import BytesIO
from pathlib import Path

from .qr import qr_url
from .types import (
    Alicuota,
    CbteTipo,
    ComprobanteAutorizado,
    Concepto,
    CondicionIVAReceptor,
    DocTipo,
    TipoTributo,
)

_CENTS = Decimal("0.01")
# Liberation Sans (SIL OFL 1.1, ver fonts/OFL.txt): Unicode y con las mismas métricas que
# Helvetica/Arial, así que el diseño no cambia respecto de las fuentes estándar de PDF.
_FONT = "LiberationSans"
_FONT_FILES = {
    "": Path(__file__).parent / "fonts" / "LiberationSans-Regular.ttf",
    "B": Path(__file__).parent / "fonts" / "LiberationSans-Bold.ttf",
}
LEYENDA_CONSUMIDOR_FINAL = "A CONSUMIDOR FINAL"
LEYENDA_TRANSPARENCIA = "Régimen de Transparencia Fiscal al Consumidor (Ley 27.743)"
# "Otros impuestos nacionales indirectos" de la leyenda de transparencia fiscal.
_IMPUESTOS_INDIRECTOS = (TipoTributo.IMPUESTOS_NACIONALES, TipoTributo.IMPUESTOS_INTERNOS)

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
    # Por defecto sale de la letra: "Responsable Monotributo" en C, "IVA Responsable
    # Inscripto" en A y B.
    condicion_iva: str | None = None


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
    # Alícuota de IVA (comprobantes A y B). None: no gravado o exento, o comprobante C.
    alicuota: Alicuota | None = None

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
        if self.alicuota is not None:
            object.__setattr__(self, "alicuota", Alicuota(self.alicuota))

    @property
    def importe_bruto(self) -> Decimal:
        return _round(self.cantidad * self.precio_unitario)

    @property
    def importe_bonificacion(self) -> Decimal:
        return _round(self.importe_bruto * self.bonificacion / 100)

    @property
    def subtotal(self) -> Decimal:
        """Importe del ítem con la bonificación aplicada (neto, sin IVA)."""
        return self.importe_bruto - self.importe_bonificacion

    @property
    def importe_iva(self) -> Decimal:
        return _iva(self.subtotal, self.alicuota)

    @property
    def subtotal_con_iva(self) -> Decimal:
        return self.subtotal + self.importe_iva


def neto_por_alicuota(items: list[Item]) -> dict[Alicuota, Decimal]:
    """Suma los subtotales de los ítems gravados por alícuota, para pasarlo como
    ``neto=`` a ``create_invoice_a`` / ``create_invoice_b``. Los ítems sin alícuota (no
    gravados o exentos) se informan aparte con ``no_gravado=`` / ``exento=``."""
    neto: dict[Alicuota, Decimal] = {}
    for item in items:
        if item.alicuota is not None:
            neto[item.alicuota] = neto.get(item.alicuota, Decimal(0)) + item.subtotal
    return {alicuota: base for alicuota, base in sorted(neto.items()) if base}


def render_invoice_pdf(
    factura: ComprobanteAutorizado,
    emisor: Emisor,
    items: list[Item],
    receptor: Receptor | None = None,
    *,
    condicion_venta: str = "Contado",
    copias: tuple[str, ...] = ("ORIGINAL",),
) -> bytes:
    """Genera el PDF del comprobante (A, B o C) y lo devuelve como bytes.

    Los `items` tienen que coincidir con lo autorizado por ARCA; si no, se lanza
    ``ValueError``. En C, la suma de los subtotales es el total sin tributos. En A y B,
    el neto de los ítems por alícuota es la base de cada alícuota autorizada y los
    ítems sin alícuota suman el no gravado más el exento.
    """
    _validar_items(factura, items)
    try:
        import segno
        from fpdf import FPDF
    except ImportError as e:  # pragma: no cover
        raise ImportError("Para generar PDFs instalá el extra: pip install arca-client[pdf]") from e

    receptor = receptor or Receptor()
    qr_png = BytesIO()
    segno.make(qr_url(factura), error="m").save(qr_png, kind="png", scale=8, border=0)

    pdf = FPDF(format="A4", unit="mm")
    for style, path in _FONT_FILES.items():
        pdf.add_font(_FONT, style, path)
    pdf.set_title(_txt(f"{_descripcion(factura.tipo)} {_numero(factura)}"))
    pdf.set_author(_txt(emisor.razon_social))
    pdf.set_auto_page_break(True, margin=15)
    pdf.set_margins(10, 10, 10)
    rangos = []
    for copia in copias:
        pdf.add_page()
        primera = pdf.page
        _encabezado(pdf, factura, emisor, copia)
        _periodo(pdf, factura)
        _asociados(pdf, factura)
        _receptor(pdf, factura, receptor, condicion_venta)
        _items(pdf, factura, items)
        _tributos(pdf, factura)
        _pie(pdf, factura, qr_png)
        rangos.append((copia, primera, pdf.page))
    _numerar_paginas(pdf, factura, rangos)
    return bytes(pdf.output())


def _validar_items(factura: ComprobanteAutorizado, items: list[Item]) -> None:
    if not items:
        raise ValueError("La factura necesita al menos un ítem")
    if _letra(factura) == "C":
        if any(item.alicuota is not None for item in items):
            raise ValueError("En comprobantes C no se discrimina IVA: los ítems no llevan alícuota")
        total_items = sum((i.subtotal for i in items), Decimal(0))
        esperado = factura.importe_total - factura.importe_tributos
        if total_items != esperado:
            raise ValueError(
                f"La suma de los ítems ({total_items}) no coincide con el importe autorizado "
                f"({esperado})"
            )
        return
    autorizado = {a.alicuota: a.base_imponible for a in factura.iva}
    por_alicuota = neto_por_alicuota(items)
    if por_alicuota != autorizado:
        raise ValueError(
            f"El neto de los ítems por alícuota ({_resumen(por_alicuota)}) no coincide con el "
            f"autorizado ({_resumen(autorizado)})"
        )
    sin_iva = sum((i.subtotal for i in items if i.alicuota is None), Decimal(0))
    esperado = factura.importe_no_gravado + factura.importe_exento
    if sin_iva != esperado:
        raise ValueError(
            f"Los ítems sin alícuota suman {sin_iva}, que no coincide con el importe autorizado "
            f"como no gravado y exento ({esperado})"
        )


def _resumen(neto: dict[Alicuota, Decimal]) -> str:
    return ", ".join(f"{_pct(a)}: {base}" for a, base in neto.items()) or "nada"


def _numerar_paginas(pdf, factura: ComprobanteAutorizado, rangos) -> None:
    """Pie en cada página para identificar las hojas de continuación."""
    ultima = pdf.page
    pdf.set_auto_page_break(False)
    pdf.set_font(_FONT, "", 7)
    for copia, primera, final in rangos:
        total = final - primera + 1
        for n, page in enumerate(range(primera, final + 1), 1):
            pdf.page = page
            pdf.set_xy(pdf.l_margin, pdf.h - 10)
            texto = f"{_descripcion(factura.tipo)} {_numero(factura)} - {copia} - Pág. {n}/{total}"
            pdf.cell(_W, 4, _txt(texto), align="R")
    pdf.page = ultima


# --- Secciones ---------------------------------------------------------------

_W = 190  # ancho útil


def _encabezado(pdf, factura: ComprobanteAutorizado, emisor: Emisor, copia: str) -> None:
    x0 = pdf.l_margin
    pdf.set_font(_FONT, "B", 12)
    pdf.cell(_W, 8, _txt(copia), border=1, align="C", new_x="LMARGIN", new_y="NEXT")

    top, height, half = pdf.get_y(), 48, _W / 2
    pdf.rect(x0, top, _W, height)
    pdf.line(x0 + half, top + 16, x0 + half, top + height)

    # Letra del comprobante
    box = 16
    pdf.set_fill_color(255, 255, 255)
    pdf.rect(x0 + half - box / 2, top, box, box, style="DF")
    pdf.set_xy(x0 + half - box / 2, top + 1)
    pdf.set_font(_FONT, "B", 24)
    pdf.cell(box, 10, _letra(factura), align="C")
    pdf.set_xy(x0 + half - box / 2, top + 10)
    pdf.set_font(_FONT, "B", 7)
    pdf.cell(box, 5, f"COD. {int(factura.tipo):03d}", align="C")

    # Emisor (izquierda)
    pdf.set_xy(x0 + 3, top + 4)
    pdf.set_font(_FONT, "B", 15)
    pdf.multi_cell(half - box / 2 - 6, 7, _txt(emisor.razon_social), align="C")
    y = max(pdf.get_y() + 2, top + 20)
    for label, value in (
        ("Razón Social", emisor.razon_social),
        ("Domicilio Comercial", emisor.domicilio),
        ("Condición frente al IVA", _condicion_emisor(emisor, factura)),
    ):
        pdf.set_xy(x0 + 3, y)
        _label_value(pdf, label, value, half - 6)
        y = pdf.get_y() + 1

    # Comprobante (derecha)
    xr = x0 + half + 4
    pdf.set_xy(xr + box / 2, top + 3)
    pdf.set_font(_FONT, "B", 18)
    pdf.cell(half - box / 2 - 6, 10, _txt(_nombre(factura.tipo).upper()))
    pdf.set_xy(xr, top + 18)
    pdf.set_font(_FONT, "B", 9)
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


def _asociados(pdf, factura: ComprobanteAutorizado) -> None:
    """Comprobantes o período a los que se refiere una nota de crédito/débito."""
    if factura.periodo_asociado:
        desde, hasta = factura.periodo_asociado
        label, value = "Período asociado", f"{_fecha(desde)} al {_fecha(hasta)}"
    elif factura.asociados:
        label = "Comprobante asociado" if len(factura.asociados) == 1 else "Comprobantes asociados"
        value = ", ".join(
            f"{_descripcion(a.tipo)} {a.punto_venta:05d}-{a.numero:08d}"
            + (f" del {_fecha(a.fecha)}" if a.fecha else "")
            for a in factura.asociados
        )
    else:
        return
    top = pdf.get_y()
    pdf.set_xy(pdf.l_margin + 3, top + 1.5)
    _label_value(pdf, label, value, _W - 6)
    bottom = max(pdf.get_y(), top + 6.5) + 1.5
    pdf.rect(pdf.l_margin, top, _W, bottom - top)
    pdf.set_xy(pdf.l_margin, bottom + 1)


def _receptor(pdf, factura: ComprobanteAutorizado, receptor: Receptor, condicion_venta) -> None:
    top = pdf.get_y()
    x0, half = pdf.l_margin, _W / 2
    pdf.set_xy(x0 + 3, top + 2)
    if factura.condicion_iva_receptor is CondicionIVAReceptor.CONSUMIDOR_FINAL:
        # Leyenda exigida por la RG 1415 (Anexo II) para receptores consumidores finales.
        pdf.set_font(_FONT, "B", 9)
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


# Anchos de columna (mm); la descripción se queda con el resto de los 190.
_ANCHOS = {
    "normal": dict(codigo=18, cantidad=20, unidad=20, precio=26, subtotal=26),
    "bonif": dict(
        codigo=16, cantidad=17, unidad=17, precio=22, bonif=16, imp_bonif=20, subtotal=24
    ),
    "A": dict(
        codigo=13,
        cantidad=16,
        unidad=17,
        precio=20,
        bonif=13,
        imp_bonif=18,
        subtotal=20,
        alicuota=12,
        con_iva=24,
    ),
}


def _items(pdf, factura: ComprobanteAutorizado, items: list[Item]) -> None:
    letra = _letra(factura)
    # Las columnas de bonificación solo se muestran si algún ítem tiene descuento.
    con_bonif = any(item.bonificacion for item in items)
    anchos = _ANCHOS["A" if letra == "A" else "bonif" if con_bonif else "normal"]

    def precio(item: Item, value: Decimal) -> str:
        # En la B el IVA no se discrimina: los precios se muestran con IVA incluido.
        return _money(value + _iva(value, item.alicuota) if letra == "B" else value)

    columnas = [
        ("Código", "codigo", "LEFT", lambda i: _txt(i.codigo)),
        ("Producto / Servicio", None, "LEFT", lambda i: _txt(i.descripcion)),
        ("Cantidad", "cantidad", "RIGHT", lambda i: _qty(i.cantidad)),
        ("U. Medida", "unidad", "CENTER", lambda i: _txt(i.unidad)),
        ("Precio Unit.", "precio", "RIGHT", lambda i: precio(i, i.precio_unitario)),
    ]
    if con_bonif:
        columnas += [
            ("% Bonif", "bonif", "RIGHT", lambda i: _money(i.bonificacion)),
            ("Imp. Bonif.", "imp_bonif", "RIGHT", lambda i: precio(i, i.importe_bonificacion)),
        ]
    columnas.append(("Subtotal", "subtotal", "RIGHT", lambda i: precio(i, i.subtotal)))
    if letra == "A":
        columnas += [
            (
                "% IVA",
                "alicuota",
                "CENTER",
                lambda i: _pct(i.alicuota) if i.alicuota else "-",
            ),
            ("Subtotal c/IVA", "con_iva", "RIGHT", lambda i: _money(i.subtotal_con_iva)),
        ]
    fijos = sum(anchos[key] for _, key, _, _ in columnas if key)
    widths = tuple(anchos[key] if key else _W - fijos for _, key, _, _ in columnas)

    pdf.set_font(_FONT, "", 8)
    pdf.set_draw_color(150, 150, 150)
    with pdf.table(
        col_widths=widths,
        text_align=tuple(align for _, _, align, _ in columnas),
        line_height=5,
        headings_style=_headings_style(),
        first_row_as_headings=True,
        repeat_headings=1,
    ) as table:
        table.row(tuple(_txt(header) for header, _, _, _ in columnas))
        for item in items:
            table.row([valor(item) for _, _, _, valor in columnas])
    pdf.set_draw_color(0, 0, 0)


def _tributos(pdf, factura: ComprobanteAutorizado) -> None:
    if not factura.tributos:
        return
    pdf.ln(3)
    pdf.set_font(_FONT, "B", 8.5)
    pdf.cell(_W, 5, "Otros tributos", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(_FONT, "", 8)
    pdf.set_draw_color(150, 150, 150)
    with pdf.table(
        col_widths=(100, 35, 20, 35),
        text_align=("LEFT", "RIGHT", "RIGHT", "RIGHT"),
        line_height=5,
        headings_style=_headings_style(),
        first_row_as_headings=True,
    ) as table:
        table.row(("Descripción", "Base imponible", "Alíc. %", "Importe"))
        for t in factura.tributos:
            table.row(
                (_txt(t.descripcion), _money(t.base_imponible), _qty(t.alicuota), _money(t.importe))
            )
    pdf.set_draw_color(0, 0, 0)


def _pie(pdf, factura: ComprobanteAutorizado, qr_png: BytesIO) -> None:
    lineas = _totales(factura)
    caja = 5 + 7 * len(lineas)
    needed = caja + 46
    if pdf.get_y() + needed > pdf.h - pdf.b_margin:
        pdf.add_page()
    x0 = pdf.l_margin
    top = pdf.h - pdf.b_margin - needed

    # Totales
    pdf.rect(x0, top, _W, caja)
    y = top + 3
    for label, value, bold in lineas:
        pdf.set_xy(x0 + 90, y)
        pdf.set_font(_FONT, "B", 10 if bold else 9)
        pdf.cell(65, 6, _txt(label), align="R")
        pdf.cell(32, 6, _money(value), align="R")
        y += 7
    if _letra(factura) == "B" and (
        factura.condicion_iva_receptor is CondicionIVAReceptor.CONSUMIDOR_FINAL
    ):
        _transparencia_fiscal(pdf, factura, x0 + 3, top + 3)

    # QR y CAE
    qr_size = 36
    qr_top = top + caja + 6
    qr_png.seek(0)
    pdf.image(qr_png, x=x0, y=qr_top, w=qr_size, h=qr_size)
    pdf.set_xy(x0 + qr_size + 6, qr_top + 4)
    pdf.set_font(_FONT, "B", 11)
    pdf.cell(80, 6, "Comprobante Autorizado")
    pdf.set_xy(x0 + 120, qr_top + 4)
    _label_value(pdf, "CAE N°", factura.cae, 70, inline=True)
    pdf.set_xy(x0 + 120, qr_top + 10)
    _label_value(pdf, "Fecha de Vto. de CAE", _fecha(factura.cae_vencimiento), 70, inline=True)


def _totales(factura: ComprobanteAutorizado) -> list[tuple[str, Decimal, bool]]:
    """Líneas del recuadro de totales. Solo la A discrimina el IVA."""
    otros = ("Importe Otros Tributos: $", factura.importe_tributos, False)
    total = ("Importe Total: $", factura.importe_total, True)
    if _letra(factura) != "A":
        subtotal = factura.importe_total - factura.importe_tributos
        return [("Subtotal: $", subtotal, False), otros, total]
    lineas = [("Importe Neto Gravado: $", factura.importe_neto, False)]
    lineas += [(f"IVA {_pct(a.alicuota)}: $", a.importe, False) for a in factura.iva]
    if factura.importe_no_gravado:
        lineas.append(("Importe No Gravado: $", factura.importe_no_gravado, False))
    if factura.importe_exento:
        lineas.append(("Importe Exento: $", factura.importe_exento, False))
    return [*lineas, otros, total]


def _transparencia_fiscal(pdf, factura: ComprobanteAutorizado, x: float, y: float) -> None:
    """Leyenda de la Ley 27.743 (RG 5614) para la Factura B a consumidores finales."""
    indirectos = sum(
        (t.importe for t in factura.tributos if t.tipo in _IMPUESTOS_INDIRECTOS), Decimal(0)
    )
    pdf.set_xy(x, y)
    pdf.set_font(_FONT, "B", 7.5)
    pdf.cell(85, 5, _txt(LEYENDA_TRANSPARENCIA))
    for i, (label, value) in enumerate(
        (
            ("IVA Contenido", factura.importe_iva),
            ("Otros Impuestos Nacionales Indirectos", indirectos),
        ),
        1,
    ):
        pdf.set_xy(x, y + 5 * i)
        _label_value(pdf, label, f"$ {_money(value)}", 85, inline=True)


# --- Helpers -----------------------------------------------------------------


def _headings_style():
    from fpdf.fonts import FontFace

    return FontFace(emphasis="BOLD", fill_color=(225, 225, 225))


def _label_value(pdf, label: str, value: str, width: float, inline: bool = False) -> None:
    pdf.set_font(_FONT, "B", 8.5)
    label_text = _txt(f"{label}: ")
    label_w = pdf.get_string_width(label_text) + 0.5
    if inline:
        pdf.cell(label_w, 5, label_text)
        pdf.set_font(_FONT, "", 8.5)
        pdf.cell(max(width - label_w, 1), 5, _txt(value))
        return
    x = pdf.get_x()
    pdf.cell(label_w, 5, label_text)
    pdf.set_font(_FONT, "", 8.5)
    pdf.multi_cell(max(width - label_w, 10), 5, _txt(value), new_x="LEFT", new_y="NEXT")
    pdf.set_x(x)


def _txt(value) -> str:
    """Reemplaza por ``?`` los caracteres que la fuente no tiene (emojis, CJK, etc.)."""
    glifos = _glifos()
    return "".join(c if ord(c) in glifos or c in "\n\r\t" else "?" for c in str(value))


@cache
def _glifos() -> frozenset[int]:
    """Caracteres que cubren a la vez la variante normal y la negrita de la fuente."""
    from fontTools.ttLib import TTFont

    regular, bold = (set(TTFont(path).getBestCmap()) for path in _FONT_FILES.values())
    return frozenset(regular & bold)


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


def _pct(alicuota: Alicuota) -> str:
    return f"{_qty(alicuota.porcentaje)}%"


def _iva(neto: Decimal, alicuota: Alicuota | None) -> Decimal:
    return _round(neto * alicuota.porcentaje / 100) if alicuota is not None else Decimal(0)


def _letra(factura: ComprobanteAutorizado) -> str:
    return CbteTipo(factura.tipo).letra


def _nombre(tipo) -> str:
    return CbteTipo(tipo).nombre if tipo in set(CbteTipo) else "Comprobante"


def _descripcion(tipo) -> str:
    return CbteTipo(tipo).descripcion if tipo in set(CbteTipo) else f"Comprobante tipo {tipo}"


def _condicion_emisor(emisor: Emisor, factura: ComprobanteAutorizado) -> str:
    if emisor.condicion_iva:
        return emisor.condicion_iva
    return "Responsable Monotributo" if _letra(factura) == "C" else "IVA Responsable Inscripto"


def _numero(factura: ComprobanteAutorizado) -> str:
    return f"{factura.punto_venta:05d}-{factura.numero:08d}"


def _decimal(value, nombre: str) -> Decimal:
    if isinstance(value, float):
        raise TypeError(f"Usá Decimal, int o str para {nombre}, no float")
    try:
        return Decimal(value)
    except InvalidOperation as e:
        raise ValueError(f"{nombre.capitalize()} inválido: {value!r}") from e
