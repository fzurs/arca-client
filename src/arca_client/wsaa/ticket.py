"""Ticket de Acceso (LoginTicketResponse.xml)."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from lxml import etree

DEFAULT_MARGIN = timedelta(minutes=5)


@dataclass(frozen=True)
class AccessTicket:
    token: str
    sign: str
    generation_time: datetime
    expiration_time: datetime
    unique_id: int | None = None
    source: str | None = None
    destination: str | None = None

    @classmethod
    def from_xml(cls, xml: str | bytes) -> "AccessTicket":
        if isinstance(xml, str):
            xml = xml.encode("utf-8")
        root = etree.fromstring(xml)
        unique_id = root.findtext("header/uniqueId")
        return cls(
            token=root.findtext("credentials/token"),
            sign=root.findtext("credentials/sign"),
            generation_time=datetime.fromisoformat(root.findtext("header/generationTime")),
            expiration_time=datetime.fromisoformat(root.findtext("header/expirationTime")),
            unique_id=int(unique_id) if unique_id else None,
            source=root.findtext("header/source"),
            destination=root.findtext("header/destination"),
        )

    def is_valid(self, now: datetime | None = None, margin: timedelta = DEFAULT_MARGIN) -> bool:
        """True si al TA le queda al menos `margin` antes de vencer.

        No se exige ``generation_time <= now``: si el reloj local atrasa respecto de ARCA,
        un TA recién emitido parecería "futuro" y se pediría otro (``coe.alreadyAuthenticated``).
        """
        now = now or datetime.now(timezone.utc)
        return now < self.expiration_time - margin

    def to_dict(self) -> dict:
        return {
            "token": self.token,
            "sign": self.sign,
            "generation_time": self.generation_time.isoformat(),
            "expiration_time": self.expiration_time.isoformat(),
            "unique_id": self.unique_id,
            "source": self.source,
            "destination": self.destination,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AccessTicket":
        return cls(
            token=data["token"],
            sign=data["sign"],
            generation_time=datetime.fromisoformat(data["generation_time"]),
            expiration_time=datetime.fromisoformat(data["expiration_time"]),
            unique_id=data.get("unique_id"),
            source=data.get("source"),
            destination=data.get("destination"),
        )
