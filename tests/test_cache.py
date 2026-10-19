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
