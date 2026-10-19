from datetime import datetime, timedelta, timezone

import pytest
from lxml import etree

from arca_client.wsaa.tra import build_tra

# XSD del TRA, tomado de la Especificación Técnica del WSAA 1.2.2.
TRA_XSD = b"""<?xml version="1.0" encoding="UTF-8"?>
<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <xsd:element name="loginTicketRequest" type="loginTicketRequest"/>
  <xsd:complexType name="loginTicketRequest">
    <xsd:sequence>
      <xsd:element name="header" type="headerType" minOccurs="1" maxOccurs="1"/>
      <xsd:element name="service" type="serviceType" minOccurs="1" maxOccurs="1"/>
    </xsd:sequence>
    <xsd:attribute name="version" type="xsd:decimal" use="optional" default="1.0"/>
  </xsd:complexType>
  <xsd:complexType name="headerType">
    <xsd:sequence>
      <xsd:element name="source" type="xsd:string" minOccurs="0" maxOccurs="1"/>
      <xsd:element name="destination" type="xsd:string" minOccurs="0" maxOccurs="1"/>
      <xsd:element name="uniqueId" type="xsd:unsignedInt" minOccurs="1" maxOccurs="1"/>
      <xsd:element name="generationTime" type="xsd:dateTime" minOccurs="1" maxOccurs="1"/>
      <xsd:element name="expirationTime" type="xsd:dateTime" minOccurs="1" maxOccurs="1"/>
    </xsd:sequence>
  </xsd:complexType>
  <xsd:simpleType name="serviceType">
    <xsd:restriction base="xsd:string">
      <xsd:minLength value="3"/>
      <xsd:maxLength value="32"/>
    </xsd:restriction>
  </xsd:simpleType>
</xsd:schema>"""

NOW = datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc)


def test_tra_valid_against_schema():
    schema = etree.XMLSchema(etree.fromstring(TRA_XSD))
    doc = etree.fromstring(build_tra("wsfe", now=NOW))
    schema.assertValid(doc)


def test_tra_fields():
    doc = etree.fromstring(build_tra("wsfe", now=NOW, ttl=timedelta(minutes=5), unique_id=42))
    assert doc.get("version") == "1.0"
    assert doc.findtext("service") == "wsfe"
    assert doc.findtext("header/uniqueId") == "42"
    assert datetime.fromisoformat(doc.findtext("header/generationTime")) == NOW - timedelta(
        minutes=5
    )
    assert datetime.fromisoformat(doc.findtext("header/expirationTime")) == NOW + timedelta(
        minutes=5
    )


def test_tra_requires_timezone():
    with pytest.raises(ValueError):
        build_tra("wsfe", now=datetime(2026, 1, 1))
