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

    El QR lo arma quien emite el comprobante, así que se valida antes de devolverlo: los
    datos numéricos tienen que ser enteros de JSON con una cantidad acotada de dígitos y
    el importe, un número con hasta 2 decimales y sin exponente. Si algo no cumple, se
    lanza ``ValueError``. Los datos ausentes no se validan acá.
    """
    valores = parse_qs(urlparse(url.strip()).query).get("p")
    if not valores:
        raise ValueError("El texto del QR no tiene el parámetro p=")
    data = valores[0].replace(" ", "+")  # parse_qs convierte los "+" sin codificar en espacios
    data += "=" * (-len(data) % 4)
    try:
        raw = base64.b64decode(data, altchars=b"-_" if "-" in data or "_" in data else None)
        payload = json.loads(raw, parse_float=_parse_float)
    except _NumeroInvalido:
        raise
    except (ValueError, binascii.Error) as e:
        raise ValueError(f"El QR no contiene un JSON válido en Base64: {e}") from e
    if not isinstance(payload, dict):
        raise ValueError("El QR no contiene un objeto JSON")
    _validar(payload)
    return payload


# Máximo de dígitos de cada dato entero del QR (especificación de la RG 4892 y WSDL de WSCDC).
_DIGITOS = {
    "ver": 2,
    "cuit": 11,
    "ptoVta": 5,
    "tipoCmp": 3,
    "nroCmp": 8,
    "tipoDocRec": 2,
    "nroDocRec": 20,
    "codAut": 14,
}
_DIGITOS_IMPORTE = 13  # enteros; más 2 decimales
_LARGO_MAXIMO_NUMERO = 40


class _NumeroInvalido(ValueError):
    pass


def _parse_float(texto: str) -> Decimal:
    # Un exponente (1e999999999) hace que convertir a int o redondear tarde horas.
    if "e" in texto or "E" in texto:
        raise _NumeroInvalido(f"El QR tiene un número con exponente: {texto[:20]!r}")
    if len(texto) > _LARGO_MAXIMO_NUMERO:
        raise _NumeroInvalido("El QR tiene un número demasiado largo")
    return Decimal(texto)


def _validar(payload: dict) -> None:
    for clave, digitos in _DIGITOS.items():
        if clave not in payload:
            continue
        valor = payload[clave]
        # bool es subclase de int; los Decimal salen de números con punto (3.0).
        if type(valor) is not int:
            raise ValueError(f"El dato {clave!r} del QR tiene que ser un número entero")
        if valor < 0 or len(str(valor)) > digitos:
            raise ValueError(
                f"El dato {clave!r} del QR tiene que ser un entero de hasta {digitos} dígitos"
            )
    if "importe" in payload:
        importe = payload["importe"]
        if type(importe) is int:
            importe = Decimal(importe)
        if not isinstance(importe, Decimal):
            raise ValueError("El importe del QR tiene que ser un número")
        _, digitos, exponente = importe.as_tuple()
        if exponente < -2 or len(digitos) + exponente > _DIGITOS_IMPORTE:
            raise ValueError(
                f"El importe del QR tiene que tener hasta {_DIGITOS_IMPORTE} enteros y 2 decimales"
            )
    if "fecha" in payload and not isinstance(payload["fecha"], str):
        raise ValueError("La fecha del QR tiene que ser un texto AAAA-MM-DD")


def _number(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)
