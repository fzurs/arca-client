"""Caché de Tickets de Acceso.

El WSAA rechaza pedir un TA nuevo mientras haya uno vigente
(``coe.alreadyAuthenticated``), así que hay que reutilizarlo durante sus 12 h.
"""

import json
import os
import re
from pathlib import Path
from typing import Protocol

from .ticket import AccessTicket


class TicketCache(Protocol):
    def get(self, key: str) -> AccessTicket | None: ...

    def set(self, key: str, ticket: AccessTicket) -> None: ...


class MemoryTicketCache:
    def __init__(self) -> None:
        self._tickets: dict[str, AccessTicket] = {}

    def get(self, key: str) -> AccessTicket | None:
        return self._tickets.get(key)

    def set(self, key: str, ticket: AccessTicket) -> None:
        self._tickets[key] = ticket


class FileTicketCache:
    """Guarda cada TA como JSON en `directory` (por defecto ``.ta_cache/``)."""

    def __init__(self, directory: str | os.PathLike = ".ta_cache") -> None:
        self.directory = Path(directory)

    def _path(self, key: str) -> Path:
        return self.directory / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', key)}.json"

    def get(self, key: str) -> AccessTicket | None:
        path = self._path(key)
        try:
            return AccessTicket.from_dict(json.loads(path.read_text()))
        except (FileNotFoundError, ValueError, KeyError):
            return None

    def set(self, key: str, ticket: AccessTicket) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(key)
        tmp = path.with_suffix(".tmp")
        # El token/sign son credenciales: solo legibles por el dueño.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(ticket.to_dict(), f)
        os.replace(tmp, path)
