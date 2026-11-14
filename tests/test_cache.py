import stat

from conftest import SPEC_RESPONSE

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
