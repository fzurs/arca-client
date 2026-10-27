from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID

from arca_client import (
    WSAA,
    ArcaError,
    Environment,
    MemoryTicketCache,
    certificate_paths,
    cuit_from_certificate,
)


@pytest.fixture
def cert_file(tmp_path, cert_pem):
    path = tmp_path / "miapp.crt"
    path.write_bytes(cert_pem)
    return path


def test_cuit_from_certificate_object(cert_pem):
    assert cuit_from_certificate(x509.load_pem_x509_certificate(cert_pem)) == "20123456780"


def test_cuit_from_certificate_bytes(cert_pem):
    assert cuit_from_certificate(cert_pem) == "20123456780"


def test_cuit_from_certificate_str_y_path(cert_file):
    assert cuit_from_certificate(str(cert_file)) == "20123456780"
    assert cuit_from_certificate(cert_file) == "20123456780"


def test_cuit_from_certificate_archivo_inexistente(tmp_path):
    with pytest.raises(FileNotFoundError):
        cuit_from_certificate(tmp_path / "no.crt")


def test_cuit_from_certificate_sin_cuit(key_pem):
    key = serialization.load_pem_private_key(key_pem, password=None)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "sincuit")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    assert cuit_from_certificate(cert) is None


def test_certificate_paths(tmp_path):
    cert, key = certificate_paths("miapp", Environment.HOMOLOGACION, tmp_path)
    assert cert == tmp_path / "homologacion" / "miapp.crt"
    assert key == tmp_path / "homologacion" / "miapp.key"


def test_certificate_paths_defaults_y_env_str():
    cert, key = certificate_paths("miapp", "produccion")
    assert cert == Path("certs/produccion/miapp.crt")
    assert key == Path("certs/produccion/miapp.key")


@pytest.mark.parametrize("alias", ["mi app", "mi-app", "", "../otro"])
def test_certificate_paths_alias_invalido(alias):
    with pytest.raises(ValueError):
        certificate_paths(alias, Environment.HOMOLOGACION)


def _write_alias(base, cert_pem, key_pem, *, cert=True, key=True):
    cert_path, key_path = certificate_paths("miapp", Environment.HOMOLOGACION, base)
    cert_path.parent.mkdir(parents=True)
    if cert:
        cert_path.write_bytes(cert_pem)
    if key:
        key_path.write_bytes(key_pem)
    return cert_path, key_path


def test_wsaa_from_alias(tmp_path, cert_pem, key_pem):
    _write_alias(tmp_path, cert_pem, key_pem)
    cache = MemoryTicketCache()
    wsaa = WSAA.from_alias("miapp", "homologacion", base_dir=tmp_path, cache=cache, timeout=5)
    assert wsaa.env is Environment.HOMOLOGACION
    assert wsaa.cache is cache
    assert wsaa.timeout == 5
    assert cuit_from_certificate(wsaa.certificate) == "20123456780"


@pytest.mark.parametrize("falta", ["cert", "key"])
def test_wsaa_from_alias_falta_archivo(tmp_path, cert_pem, key_pem, falta):
    cert_path, key_path = _write_alias(
        tmp_path, cert_pem, key_pem, cert=falta != "cert", key=falta != "key"
    )
    missing = cert_path if falta == "cert" else key_path
    with pytest.raises(ArcaError, match="arca-setup") as excinfo:
        WSAA.from_alias("miapp", base_dir=tmp_path)
    assert str(missing) in str(excinfo.value)
