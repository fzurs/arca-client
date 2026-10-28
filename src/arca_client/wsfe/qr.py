"""Código QR de comprobantes electrónicos (RG 4892, especificación versión 1)."""

import base64
import binascii
import json
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

from .types import ComprobanteAutorizado, DocTipo

QR_BASE_URL = "https://www.arca.gob.ar/fe/qr/"


def qr_payload(factura: ComprobanteAutorizado) -> dict:
    payload = {
        "ver": 1,
        "fecha": factura.fecha.isoformat(),
        "cuit": factura.cuit,
        "ptoVta": factura.punto_venta,
        "tipoCmp": int(factura.tipo),
        "nroCmp": factura.numero,
        "importe": _number(factura.importe_total),
        "moneda": "PES",
        "ctz": 1,
    }
    # "De corresponder": solo si el receptor está identificado.
    if factura.doc_tipo is not DocTipo.CONSUMIDOR_FINAL and factura.doc_nro:
        payload["tipoDocRec"] = int(factura.doc_tipo)
        payload["nroDocRec"] = factura.doc_nro
    payload["tipoCodAut"] = "E"  # CAE
    payload["codAut"] = int(factura.cae)
    return payload


def qr_url(factura: ComprobanteAutorizado) -> str:
    """Texto a codificar en el QR: ``https://www.arca.gob.ar/fe/qr/?p=<JSON en Base64>``."""
    data = json.dumps(qr_payload(factura), separators=(",", ":"))
    return f"{QR_BASE_URL}?p={base64.b64encode(data.encode()).decode('ascii')}"


def parse_qr_url(url: str) -> dict:
    """Datos del QR de un comprobante (el JSON de ``?p=``), con los importes como ``Decimal``.

    Acepta cualquier dominio (``arca.gob.ar``, ``afip.gob.ar``) y Base64 con o sin relleno.
    """
    valores = parse_qs(urlparse(url.strip()).query).get("p")
    if not valores:
        raise ValueError("El texto del QR no tiene el parámetro p=")
    data = valores[0].replace(" ", "+")  # parse_qs convierte los "+" sin codificar en espacios
    data += "=" * (-len(data) % 4)
    try:
        raw = base64.b64decode(data, altchars=b"-_" if "-" in data or "_" in data else None)
        payload = json.loads(raw, parse_float=Decimal)
    except (ValueError, binascii.Error) as e:
        raise ValueError(f"El QR no contiene un JSON válido en Base64: {e}") from e
    if not isinstance(payload, dict):
        raise ValueError("El QR no contiene un objeto JSON")
    return payload


def _number(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)
