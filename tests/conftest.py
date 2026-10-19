from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


@pytest.fixture(scope="session")
def key_pem():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


@pytest.fixture(scope="session")
def cert_pem(key_pem):
    key = serialization.load_pem_private_key(key_pem, password=None)
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "test"),
            x509.NameAttribute(NameOID.SERIAL_NUMBER, "CUIT 20123456780"),
        ]
    )
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)


SPEC_RESPONSE = """<?xml version="1.0" encoding="UTF-8"?>
<loginTicketResponse version="1.0">
  <header>
    <source>cn=wsaa,o=afip,c=ar,serialNumber=CUIT 33693450239</source>
    <destination>cn=srv1,o=empresa s.a.,c=ar,serialNumber=CUIT 30123456789</destination>
    <uniqueId>383953094</uniqueId>
    <generationTime>2001-12-31T12:00:00-03:00</generationTime>
    <expirationTime>2002-01-01T00:00:00-03:00</expirationTime>
  </header>
  <credentials>
    <token>PD94bWwgdmVyc2lvbj0iMS4wIj8+</token>
    <sign>a6QSSZBgLf0TTcktSNteeSg3qXsMVjo</sign>
  </credentials>
</loginTicketResponse>"""


def make_response(generation: datetime, expiration: datetime, token: str = "TOKEN") -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<loginTicketResponse version="1.0">
  <header>
    <source>cn=wsaahomo,o=afip,c=ar,serialNumber=CUIT 33693450239</source>
    <destination>cn=test</destination>
    <uniqueId>1</uniqueId>
    <generationTime>{generation.isoformat()}</generationTime>
    <expirationTime>{expiration.isoformat()}</expirationTime>
  </header>
  <credentials><token>{token}</token><sign>SIGN</sign></credentials>
</loginTicketResponse>"""
