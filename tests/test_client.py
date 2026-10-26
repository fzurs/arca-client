import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from conftest import make_response
from zeep.exceptions import Fault

from arca_client import WSAA, Environment, FileTicketCache, MemoryTicketCache, WSAAError


@pytest.fixture
def wsaa(cert_pem, key_pem):
    return WSAA(cert_pem, key_pem, Environment.HOMOLOGACION, cache=MemoryTicketCache())


def _valid_response(token="TOKEN"):
    now = datetime.now(timezone.utc)
    return make_response(now, now + timedelta(hours=12), token)


def test_get_ticket_uses_cache(wsaa, monkeypatch):
    calls = []

    def fake_login_cms(cms):
        calls.append(cms)
        return _valid_response(f"T{len(calls)}")

    monkeypatch.setattr(wsaa, "_login_cms", fake_login_cms)

    first = wsaa.get_ticket("wsfe")
    second = wsaa.get_ticket("wsfe")
    assert first.token == second.token == "T1"
    assert len(calls) == 1

    assert wsaa.get_ticket("wsfe", force=True).token == "T2"
    assert wsaa.get_ticket("ws_sr_padron_a13").token == "T3"


def test_expired_ticket_is_renewed(wsaa, monkeypatch):
    now = datetime.now(timezone.utc)
    responses = iter([make_response(now - timedelta(hours=12), now, "OLD"), _valid_response("NEW")])
    monkeypatch.setattr(wsaa, "_login_cms", lambda cms: next(responses))

    assert wsaa.get_ticket("wsfe").token == "OLD"
    assert wsaa.get_ticket("wsfe").token == "NEW"


def test_ticket_reused_when_local_clock_is_behind(wsaa, monkeypatch):
    # generationTime de ARCA 30 s adelante del reloj local: no debe pedirse otro TA.
    generation = datetime.now(timezone.utc) + timedelta(seconds=30)
    calls = []

    def fake_login_cms(cms):
        calls.append(cms)
        return make_response(generation, generation + timedelta(hours=12), f"T{len(calls)}")

    monkeypatch.setattr(wsaa, "_login_cms", fake_login_cms)

    assert wsaa.get_ticket("wsfe").token == "T1"
    assert wsaa.get_ticket("wsfe").token == "T1"
    assert len(calls) == 1


def _fake_arca():
    """loginCms como ARCA: emite un solo TA; los pedidos siguientes, alreadyAuthenticated."""
    calls = []
    lock = threading.Lock()

    def login_cms(cms):
        time.sleep(0.1)  # da tiempo a que los pedidos se superpongan
        with lock:
            calls.append(cms)
            if len(calls) > 1:
                raise Fault("ya posee un TA valido", code="ns1:coe.alreadyAuthenticated")
        return _valid_response("T1")

    return login_cms, calls


def _concurrent_tokens(make_wsaa, n=4):
    tokens, errors = [], []

    def run():
        try:
            tokens.append(make_wsaa().get_ticket("wsfe").token)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=run) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return tokens, errors


def test_concurrent_requests_share_ticket(cert_pem, key_pem, tmp_path):
    # Cada instancia con su propio FileTicketCache, como procesos distintos.
    login_cms, calls = _fake_arca()

    def make_wsaa():
        w = WSAA(cert_pem, key_pem, Environment.HOMOLOGACION, cache=FileTicketCache(tmp_path))
        w._login_cms = login_cms
        return w

    tokens, errors = _concurrent_tokens(make_wsaa)
    assert errors == []
    assert tokens == ["T1"] * 4
    assert len(calls) == 1


def test_concurrent_requests_share_memory_cache(cert_pem, key_pem):
    login_cms, calls = _fake_arca()
    wsaa = WSAA(cert_pem, key_pem, Environment.HOMOLOGACION, cache=MemoryTicketCache())
    wsaa._login_cms = login_cms

    tokens, errors = _concurrent_tokens(lambda: wsaa)
    assert errors == []
    assert tokens == ["T1"] * 4
    assert len(calls) == 1


def test_already_authenticated_rereads_cache(cert_pem, key_pem):
    # Backend propio sin lock(): otro proceso guardó el TA mientras pedíamos el nuestro.
    cache = MemoryTicketCache()
    wsaa = WSAA(cert_pem, key_pem, Environment.HOMOLOGACION, cache=cache)

    class CacheWithoutLock:
        get = staticmethod(cache.get)
        set = staticmethod(cache.set)

    wsaa.cache = CacheWithoutLock()

    def login_cms(cms):
        other = WSAA(cert_pem, key_pem, Environment.HOMOLOGACION, cache=cache)
        other._login_cms = lambda cms: _valid_response("OTHER")
        other.get_ticket("wsfe")
        raise Fault("ya posee un TA valido", code="ns1:coe.alreadyAuthenticated")

    wsaa._login_cms = login_cms
    assert wsaa.get_ticket("wsfe").token == "OTHER"


def test_fault_is_translated(wsaa, monkeypatch):
    def raise_fault(cms):
        raise Fault("Certificado expirado", code="ns1:cms.cert.expired")

    monkeypatch.setattr(wsaa, "_login_cms", raise_fault)
    with pytest.raises(WSAAError) as exc:
        wsaa.get_ticket("wsfe")
    assert exc.value.code == "cms.cert.expired"
    assert not exc.value.retryable


def test_already_authenticated_message(wsaa, monkeypatch):
    def raise_fault(cms):
        raise Fault("ya posee un TA valido", code="ns1:coe.alreadyAuthenticated")

    monkeypatch.setattr(wsaa, "_login_cms", raise_fault)
    with pytest.raises(WSAAError, match="esperar a que expire") as exc:
        wsaa.get_ticket("wsfe")
    assert exc.value.code == "coe.alreadyAuthenticated"
