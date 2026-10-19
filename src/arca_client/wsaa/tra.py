"""Generación del Ticket de Requerimiento de Acceso (LoginTicketRequest.xml)."""

from datetime import datetime, timedelta, timezone

from lxml import etree

DEFAULT_TTL = timedelta(minutes=10)


def build_tra(
    service: str,
    now: datetime | None = None,
    ttl: timedelta = DEFAULT_TTL,
    unique_id: int | None = None,
) -> bytes:
    """Devuelve el XML del TRA para `service`.

    `generationTime` se fija en ``now - ttl`` y `expirationTime` en ``now + ttl``
    para tolerar desfasajes de reloj (la spec admite hasta 24 h en cada sentido).
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("`now` debe tener zona horaria")
    if unique_id is None:
        unique_id = int(now.timestamp())

    root = etree.Element("loginTicketRequest", version="1.0")
    header = etree.SubElement(root, "header")
    etree.SubElement(header, "uniqueId").text = str(unique_id & 0xFFFFFFFF)
    etree.SubElement(header, "generationTime").text = _iso(now - ttl)
    etree.SubElement(header, "expirationTime").text = _iso(now + ttl)
    etree.SubElement(root, "service").text = service
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()
