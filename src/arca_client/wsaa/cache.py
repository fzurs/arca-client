"""Caché de Tickets de Acceso.

El WSAA rechaza pedir un TA nuevo mientras haya uno vigente
(``coe.alreadyAuthenticated``), así que hay que reutilizarlo durante sus 12 h.
"""

import contextlib
import json
import os
import re
import stat
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt

from ..exceptions import ArcaError
from .ticket import AccessTicket

# Abrir sin seguir symlinks donde el sistema lo permite (no existe en Windows).
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class TicketCache(Protocol):
    """Backend del caché.

    Opcionalmente puede tener ``lock(key)``, un context manager que serializa a quienes
    piden un TA para la misma clave (ver :meth:`FileTicketCache.lock`).
    """

    def get(self, key: str) -> AccessTicket | None: ...

    def set(self, key: str, ticket: AccessTicket) -> None: ...


class MemoryTicketCache:
    def __init__(self) -> None:
        self._tickets: dict[str, AccessTicket] = {}
        self._lock = threading.Lock()

    @contextmanager
    def lock(self, key: str) -> Iterator[None]:
        with self._lock:
            yield

    def get(self, key: str) -> AccessTicket | None:
        return self._tickets.get(key)

    def set(self, key: str, ticket: AccessTicket) -> None:
        self._tickets[key] = ticket


def default_cache_dir() -> Path:
    """Ruta fija, independiente del directorio de trabajo.

    Orden: ``$ARCA_TA_CACHE``, ``$XDG_CACHE_HOME/arca-client/tickets``,
    ``~/.cache/arca-client/tickets``.
    """
    if env := os.environ.get("ARCA_TA_CACHE"):
        return Path(env).expanduser().resolve()
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base).expanduser().resolve() / "arca-client" / "tickets"


class FileTicketCache:
    """Guarda cada TA como JSON en `directory` (por defecto :func:`default_cache_dir`).

    El token y el sign son credenciales, así que la carpeta tiene que ser privada: si
    no existe se crea con permisos 0700; si ya existe, tiene que ser un directorio
    real (no un symlink), del usuario actual y sin permisos para grupo ni otros. Si no,
    se lanza :class:`ArcaError` (no se corrigen los permisos solos). En Windows no se
    verifican dueño ni permisos.
    """

    def __init__(self, directory: str | os.PathLike | None = None) -> None:
        self.directory = (
            Path(directory).expanduser().resolve() if directory else default_cache_dir()
        )

    def _path(self, key: str) -> Path:
        return self.directory / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', key)}.json"

    @contextmanager
    def lock(self, key: str) -> Iterator[None]:
        """Bloqueo exclusivo sobre ``<key>.lock``, entre procesos e hilos.

        Así, si varios piden el TA a la vez, solo uno llama al WSAA y los demás lo
        leen del caché (si no, ARCA les responde ``coe.alreadyAuthenticated``).
        El sistema operativo lo libera aunque el proceso muera.
        """
        self._check_directory(create=True)
        lock_path = self._path(key).with_suffix(".lock")
        fd = _open_no_follow(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            _lock_file(fd)
            yield
        finally:
            os.close(fd)  # cerrar el descriptor libera el bloqueo

    def get(self, key: str) -> AccessTicket | None:
        if not self._check_directory(create=False):
            return None
        path = self._path(key)
        try:
            fd = _open_no_follow(path, os.O_RDONLY)
        except FileNotFoundError:
            return None
        with os.fdopen(fd) as f:
            try:
                return AccessTicket.from_dict(json.loads(f.read()))
            except (ValueError, KeyError):
                return None

    def set(self, key: str, ticket: AccessTicket) -> None:
        self._check_directory(create=True)
        path = self._path(key)
        # Temporal con nombre único (O_EXCL y 0600, el token/sign son credenciales): en
        # un directorio compartido, nadie puede anticiparse plantando un symlink.
        fd, tmp = tempfile.mkstemp(dir=self.directory, prefix=f".{path.stem}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(ticket.to_dict(), f)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise

    def _check_directory(self, *, create: bool) -> bool:
        """Verifica que la carpeta sea privada. Devuelve ``False`` si no existe (y no se crea)."""
        if create:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            st = os.lstat(self.directory)
        except FileNotFoundError:
            if create:
                raise
            return False
        carpeta = f"La carpeta de la caché de tickets {self.directory}"
        if not stat.S_ISDIR(st.st_mode):
            raise ArcaError(
                f"{carpeta} no es un directorio (¿es un symlink?). Usá una carpeta propia, "
                "por ejemplo con ARCA_TA_CACHE."
            )
        if not hasattr(os, "getuid"):  # Windows
            return True
        if st.st_uid != os.getuid():
            raise ArcaError(
                f"{carpeta} pertenece a otro usuario (uid {st.st_uid}). Usá una carpeta "
                "propia, por ejemplo con ARCA_TA_CACHE."
            )
        if st.st_mode & 0o077:
            raise ArcaError(
                f"{carpeta} tiene permisos {stat.S_IMODE(st.st_mode):o}: otros usuarios "
                f"podrían leer o plantar tickets. Hacela privada con `chmod 700 {self.directory}`."
            )
        return True


def _open_no_follow(path: Path, flags: int, mode: int = 0o600) -> int:
    try:
        return os.open(path, flags | _O_NOFOLLOW, mode)
    except FileNotFoundError:
        raise
    except OSError as e:
        if os.path.islink(path):
            raise ArcaError(
                f"{path} es un symlink: no se sigue por seguridad. Borralo si no lo creaste vos."
            ) from e
        raise


def _lock_file(fd: int) -> None:
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_EX)
        return
    # msvcrt.LK_LOCK se rinde a los 10 s; se reintenta hasta obtenerlo.
    while True:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return
        except OSError:
            time.sleep(0.1)
