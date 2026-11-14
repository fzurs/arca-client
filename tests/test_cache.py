import os
import stat

import pytest
from conftest import SPEC_RESPONSE

from arca_client import ArcaError
from arca_client.wsaa.cache import FileTicketCache
from arca_client.wsaa.ticket import AccessTicket


def test_file_cache_roundtrip(tmp_path):
    cache = FileTicketCache(tmp_path)
    ticket = AccessTicket.from_xml(SPEC_RESPONSE)
    assert cache.get("homologacion-wsfe-abc") is None

    cache.set("homologacion-wsfe-abc", ticket)
    assert cache.get("homologacion-wsfe-abc") == ticket

    (path,) = tmp_path.iterdir()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_file_cache_ignores_corrupt(tmp_path):
    cache = FileTicketCache(tmp_path)
    (tmp_path / "k.json").write_text("{not json")
    assert cache.get("k") is None


def test_default_dir_is_independent_of_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("ARCA_TA_CACHE", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.chdir(tmp_path)
    first = FileTicketCache().directory

    other = tmp_path / "otro"
    other.mkdir()
    monkeypatch.chdir(other)
    assert FileTicketCache().directory == first == tmp_path / "xdg" / "arca-client" / "tickets"


def test_env_var_overrides_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCA_TA_CACHE", str(tmp_path / "custom"))
    assert FileTicketCache().directory == tmp_path / "custom"


def test_relative_dir_is_resolved(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cache = FileTicketCache("rel")
    monkeypatch.chdir("/")
    assert cache.directory == tmp_path / "rel"


def test_lock_file_is_private(tmp_path):
    cache = FileTicketCache(tmp_path / "tickets")
    with cache.lock("homologacion-wsfe-abc"):
        pass
    (path,) = (tmp_path / "tickets").iterdir()
    assert path.name == "homologacion-wsfe-abc.lock"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_set_no_sigue_symlink_plantado(tmp_path):
    # Antes se escribía siempre en "<key>.tmp": un symlink ahí redirigía las credenciales.
    cache = FileTicketCache(tmp_path)
    victima = tmp_path / "victima"
    victima.write_text("intacto")
    (tmp_path / "k.tmp").symlink_to(victima)
    cache.set("k", AccessTicket.from_xml(SPEC_RESPONSE))
    assert victima.read_text() == "intacto"
    assert cache.get("k") is not None
    assert {p.name for p in tmp_path.iterdir()} == {"k.json", "k.tmp", "victima"}


# --- Carpeta compartida: no se usa si no es privada ----------------------------------

solo_posix = pytest.mark.skipif(not hasattr(os, "getuid"), reason="sin dueño/permisos POSIX")


@solo_posix
@pytest.mark.parametrize("modo", [0o755, 0o770, 0o707])
def test_carpeta_con_permisos_para_otros(tmp_path, modo):
    carpeta = tmp_path / "compartida"
    carpeta.mkdir()
    carpeta.chmod(modo)
    cache = FileTicketCache(carpeta)
    ticket = AccessTicket.from_xml(SPEC_RESPONSE)
    for usar in (lambda: cache.get("k"), lambda: cache.set("k", ticket)):
        with pytest.raises(ArcaError, match="chmod 700"):
            usar()
    with pytest.raises(ArcaError, match="chmod 700"), cache.lock("k"):
        pass
    assert stat.S_IMODE(carpeta.stat().st_mode) == modo  # no se corrigen solos
    assert not list(carpeta.iterdir())


@solo_posix
def test_carpeta_de_otro_usuario(tmp_path, monkeypatch):
    cache = FileTicketCache(tmp_path / "tickets")
    cache.set("k", AccessTicket.from_xml(SPEC_RESPONSE))
    monkeypatch.setattr(os, "getuid", lambda: os.stat(tmp_path).st_uid + 1)
    with pytest.raises(ArcaError, match="otro usuario"):
        cache.get("k")


@solo_posix
def test_carpeta_que_es_symlink(tmp_path):
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    enlace = tmp_path / "enlace"
    enlace.symlink_to(real)
    cache = FileTicketCache(tmp_path)
    cache.directory = enlace  # resolve() del constructor lo seguiría
    with pytest.raises(ArcaError, match="no es un directorio"):
        cache.get("k")


@solo_posix
def test_lock_no_sigue_symlink(tmp_path):
    victima = tmp_path / "victima"
    carpeta = tmp_path / "tickets"
    carpeta.mkdir(mode=0o700)
    (carpeta / "k.lock").symlink_to(victima)
    with pytest.raises(ArcaError, match="symlink"), FileTicketCache(carpeta).lock("k"):
        pass
    assert not victima.exists()


@solo_posix
def test_get_no_sigue_symlink(tmp_path):
    ajeno = tmp_path / "ticket_ajeno.json"
    FileTicketCache(tmp_path / "otra").set("k", AccessTicket.from_xml(SPEC_RESPONSE))
    (tmp_path / "otra" / "k.json").rename(ajeno)
    carpeta = tmp_path / "tickets"
    carpeta.mkdir(mode=0o700)
    (carpeta / "k.json").symlink_to(ajeno)
    with pytest.raises(ArcaError, match="symlink"):
        FileTicketCache(carpeta).get("k")


def test_carpeta_nueva_se_crea_privada(tmp_path):
    carpeta = tmp_path / "nueva" / "tickets"
    cache = FileTicketCache(carpeta)
    assert cache.get("k") is None and not carpeta.exists()
    ticket = AccessTicket.from_xml(SPEC_RESPONSE)
    cache.set("k", ticket)
    with cache.lock("k"):
        assert cache.get("k") == ticket
    if hasattr(os, "getuid"):
        assert stat.S_IMODE(carpeta.stat().st_mode) == 0o700
