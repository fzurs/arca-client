import stat

import pytest
from cryptography.x509.oid import NameOID

from arca_client.certificates import (
    build_csr,
    certificate_matches_key,
    generate_private_key,
    is_valid_alias,
    is_valid_cuit,
    write_private,
)


@pytest.mark.parametrize("cuit", ["20123456786", "20-12345678-6", "30-71234567-1", "33693450239"])
def test_valid_cuit(cuit):
    assert is_valid_cuit(cuit)


@pytest.mark.parametrize("cuit", ["20123456780", "2012345678", "abc", "201234567861"])
def test_invalid_cuit(cuit):
    assert not is_valid_cuit(cuit)


def test_alias():
    assert is_valid_alias("miApp2")
    assert not is_valid_alias("mi app")
    assert not is_valid_alias("mi-app")


def test_csr_subject():
    key = generate_private_key()
    csr = build_csr(key, "20-12345678-6", "Empresa SA", "miapp")
    assert csr.is_signature_valid
    subject = csr.subject
    assert subject.get_attributes_for_oid(NameOID.COUNTRY_NAME)[0].value == "AR"
    assert subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)[0].value == "Empresa SA"
    assert subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "miapp"
    serial = subject.get_attributes_for_oid(NameOID.SERIAL_NUMBER)[0].value
    assert serial == "CUIT 20123456786"


def test_certificate_matches_key(cert_pem, key_pem):
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_private_key(key_pem, password=None)
    assert certificate_matches_key(cert_pem, key)
    assert not certificate_matches_key(cert_pem, generate_private_key())


def test_write_private(tmp_path):
    path = tmp_path / "sub" / "k.key"
    write_private(path, b"secret")
    assert path.read_bytes() == b"secret"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
