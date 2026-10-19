"""Código QR de comprobantes electrónicos (RG 4892, especificación versión 1)."""

import base64
import json
from decimal import Decimal

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


def _number(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)
