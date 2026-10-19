import base64
import json
from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from pypdf import PdfReader

from arca_client import (
    CbteTipo,
    ComprobanteAutorizado,
    Concepto,
    CondicionIVAReceptor,
    DocTipo,
    Emisor,
    Item,
    Receptor,
    qr_url,
    render_invoice_pdf,
)
from arca_client.wsfe.qr import qr_payload

# Ejemplo de la especificación oficial del QR (versión 1).
SPEC_JSON = {
    "ver": 1,
    "fecha": "2020-10-13",
    "cuit": 30000000007,
    "ptoVta": 10,
    "tipoCmp": 1,
    "nroCmp": 94,
    "importe": 12100,
    "moneda": "DOL",
    "ctz": 65,
    "tipoDocRec": 80,
    "nroDocRec": 20000000001,
    "tipoCodAut": "E",
    "codAut": 70417054367476,
}
SPEC_B64 = (
    "eyJ2ZXIiOjEsImZlY2hhIjoiMjAyMC0xMC0xMyIsImN1aXQiOjMwMDAwMDAwMDA3LCJwdG9WdGEiOjEwLCJ0aXBvQ21w"
    "IjoxLCJucm9DbXAiOjk0LCJpbXBvcnRlIjoxMjEwMCwibW9uZWRhIjoiRE9MIiwiY3R6Ijo2NSwidGlwb0RvY1JlYyI6"
    "ODAsIm5yb0RvY1JlYyI6MjAwMDAwMDAwMDEsInRpcG9Db2RBdXQiOiJFIiwiY29kQXV0Ijo3MDQxNzA1NDM2NzQ3Nn0="
)


def _factura(**overrides):
    datos = dict(
        cuit=20123456786,
        punto_venta=3,
        tipo=CbteTipo.FACTURA_C,
        numero=42,
        fecha=date(2026, 10, 19),
        importe_total=Decimal("1500.50"),
        cae="76123456789012",
        cae_vencimiento=date(2026, 10, 29),
        concepto=Concepto.PRODUCTOS,
        doc_tipo=DocTipo.CONSUMIDOR_FINAL,
        doc_nro=0,
        condicion_iva_receptor=CondicionIVAReceptor.CONSUMIDOR_FINAL,
    )
    return ComprobanteAutorizado(**(datos | overrides))


EMISOR = Emisor("Juan Pérez", "Av. Siempre Viva 742", date(2020, 3, 1), "20-12345678-6")


def _decode(url):
    assert url.startswith("https://www.arca.gob.ar/fe/qr/?p=")
    return json.loads(base64.b64decode(url.split("?p=")[1]))


def test_encoding_matches_spec_example():
    data = json.dumps(SPEC_JSON, separators=(",", ":")).encode()
    assert base64.b64encode(data).decode() == SPEC_B64


def test_qr_consumidor_final():
    payload = _decode(qr_url(_factura()))
    assert payload == {
        "ver": 1,
        "fecha": "2026-10-19",
        "cuit": 20123456786,
        "ptoVta": 3,
        "tipoCmp": 11,
        "nroCmp": 42,
        "importe": 1500.5,
        "moneda": "PES",
        "ctz": 1,
        "tipoCodAut": "E",
        "codAut": 76123456789012,
    }


def test_qr_receptor_identificado_e_importe_entero():
    f = _factura(doc_tipo=DocTipo.CUIT, doc_nro=30712345671, importe_total=Decimal("12100.00"))
    payload = qr_payload(f)
    assert payload["tipoDocRec"] == 80
    assert payload["nroDocRec"] == 30712345671
    assert payload["importe"] == 12100 and isinstance(payload["importe"], int)


def _text(pdf_bytes):
    return "\n".join(p.extract_text() for p in PdfReader(BytesIO(pdf_bytes)).pages)


def test_pdf_contenido():
    f = _factura(
        concepto=Concepto.SERVICIOS,
        doc_tipo=DocTipo.CUIT,
        doc_nro=30712345671,
        condicion_iva_receptor=CondicionIVAReceptor.IVA_RESPONSABLE_INSCRIPTO,
        servicio_desde=date(2026, 10, 1),
        servicio_hasta=date(2026, 10, 31),
        vencimiento_pago=date(2026, 10, 29),
    )
    items = [Item("Diseño", "1000.00"), Item("Horas", "250.25", cantidad=2, unidad="horas")]
    pdf = render_invoice_pdf(f, EMISOR, items, Receptor("Empresa S.A.", "San Martín 500"))
    text = _text(pdf)
    for expected in (
        "ORIGINAL",
        "FACTURA",
        "COD. 011",
        "Punto de Venta: 00003",
        "Comp. Nro: 00000042",
        "19/10/2026",
        "20-12345678-6",
        "Responsable Monotributo",
        "30-71234567-1",
        "IVA Responsable Inscripto",
        "Empresa S.A.",
        "01/10/2026",
        "31/10/2026",
        "500,50",
        "1.500,50",
        "76123456789012",
        "29/10/2026",
        "Pág. 1/1",
    ):
        assert expected in text, expected


def test_pdf_copias():
    pdf = render_invoice_pdf(
        _factura(), EMISOR, [Item("X", "1500.50")], copias=("ORIGINAL", "DUPLICADO")
    )
    reader = PdfReader(BytesIO(pdf))
    assert len(reader.pages) == 2
    assert "DUPLICADO" in reader.pages[1].extract_text()


def test_pdf_items_deben_sumar_el_total():
    with pytest.raises(ValueError, match="no coincide"):
        render_invoice_pdf(_factura(), EMISOR, [Item("X", "1000")])
    with pytest.raises(ValueError):
        render_invoice_pdf(_factura(), EMISOR, [])


def test_item_validaciones():
    assert Item("X", "10.005", cantidad=1).subtotal == Decimal("10.00")  # half even
    with pytest.raises(TypeError):
        Item("X", 10.5)
    with pytest.raises(ValueError):
        Item("X", "10", cantidad=0)


def test_pdf_caracteres_fuera_de_latin1():
    pdf = render_invoice_pdf(_factura(), EMISOR, [Item("Café ☕ – “especial”", "1500.50")])
    assert 'Café ? - "especial"' in _text(pdf)


def test_leyenda_consumidor_final():
    text = _text(render_invoice_pdf(_factura(), EMISOR, [Item("X", "1500.50")]))
    assert "A CONSUMIDOR FINAL" in text

    identificado = _factura(doc_tipo=DocTipo.DNI, doc_nro=30111222)
    text = _text(render_invoice_pdf(identificado, EMISOR, [Item("X", "1500.50")]))
    assert "A CONSUMIDOR FINAL" in text
    assert "30111222" in text

    inscripto = _factura(
        doc_tipo=DocTipo.CUIT,
        doc_nro=30712345671,
        condicion_iva_receptor=CondicionIVAReceptor.IVA_RESPONSABLE_INSCRIPTO,
    )
    text = _text(render_invoice_pdf(inscripto, EMISOR, [Item("X", "1500.50")]))
    assert "A CONSUMIDOR FINAL" not in text


def test_item_bonificacion():
    item = Item("X", "1000.00", cantidad=3, bonificacion="10")
    assert item.importe_bruto == Decimal("3000.00")
    assert item.importe_bonificacion == Decimal("300.00")
    assert item.subtotal == Decimal("2700.00")

    # Redondeo half even sobre el importe de la bonificación: 0.125 -> 0.12
    item = Item("X", "1.25", bonificacion="10")
    assert item.importe_bonificacion == Decimal("0.12")
    assert item.subtotal == Decimal("1.13")

    for invalida in ("-1", "100.01"):
        with pytest.raises(ValueError):
            Item("X", "10", bonificacion=invalida)
    assert Item("X", "10", bonificacion=100).subtotal == 0


def test_pdf_columnas_bonificacion():
    sin = _text(render_invoice_pdf(_factura(), EMISOR, [Item("X", "1500.50")]))
    assert "% Bonif" not in sin

    items = [Item("Con descuento", "1000.00", bonificacion="15"), Item("Sin", "650.50")]
    con = _text(render_invoice_pdf(_factura(), EMISOR, items))
    assert "% Bonif" in con and "Imp. Bonif." in con
    assert "15,00" in con and "150,00" in con and "850,00" in con


def test_pdf_total_con_bonificacion_debe_coincidir():
    # 1000 con 10 % = 900, no coincide con 1500.50
    with pytest.raises(ValueError, match="no coincide"):
        render_invoice_pdf(_factura(), EMISOR, [Item("X", "1000", bonificacion=10)])
