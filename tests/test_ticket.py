from datetime import datetime, timedelta, timezone

from conftest import SPEC_RESPONSE

from arca_client.wsaa.ticket import AccessTicket


def test_parse_spec_example():
    ticket = AccessTicket.from_xml(SPEC_RESPONSE)
    assert ticket.token == "PD94bWwgdmVyc2lvbj0iMS4wIj8+"
    assert ticket.sign == "a6QSSZBgLf0TTcktSNteeSg3qXsMVjo"
    assert ticket.unique_id == 383953094
    assert ticket.expiration_time - ticket.generation_time == timedelta(hours=12)
    assert ticket.source.startswith("cn=wsaa")


def test_is_valid():
    ticket = AccessTicket.from_xml(SPEC_RESPONSE)
    inside = ticket.generation_time + timedelta(hours=1)
    assert ticket.is_valid(now=inside)
    assert not ticket.is_valid(now=ticket.expiration_time - timedelta(minutes=1))
    assert not ticket.is_valid(now=datetime.now(timezone.utc))


def test_is_valid_with_local_clock_behind():
    ticket = AccessTicket.from_xml(SPEC_RESPONSE)
    assert ticket.is_valid(now=ticket.generation_time - timedelta(seconds=30))


def test_dict_roundtrip():
    ticket = AccessTicket.from_xml(SPEC_RESPONSE)
    assert AccessTicket.from_dict(ticket.to_dict()) == ticket
