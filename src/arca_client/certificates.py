"""Generación de clave privada y CSR para obtener certificados de ARCA."""

import os
import re
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

_CUIT_WEIGHTS = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)
_ALIAS_RE = re.compile(r"^[A-Za-z0-9]+$")


def normalize_cuit(cuit: str) -> str:
    """Quita guiones y espacios: ``20-12345678-6`` -> ``20123456786``."""
    return re.sub(r"[\s-]", "", cuit)


def is_valid_cuit(cuit: str) -> bool:
    """Valida longitud y dígito verificador (módulo 11)."""
    cuit = normalize_cuit(cuit)
    if not re.fullmatch(r"\d{11}", cuit):
        return False
    total = sum(int(d) * w for d, w in zip(cuit[:10], _CUIT_WEIGHTS, strict=True))
    check = 11 - total % 11
    if check == 11:
        check = 0
    return check != 10 and check == int(cuit[10])


def is_valid_alias(alias: str) -> bool:
    """ARCA solo admite letras y números en el nombre del certificado (CN)."""
    return bool(_ALIAS_RE.fullmatch(alias))


def generate_private_key(key_size: int = 2048) -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=key_size)


def private_key_pem(key, password: bytes | None = None) -> bytes:
    encryption = (
        serialization.BestAvailableEncryption(password)
        if password
        else serialization.NoEncryption()
    )
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        encryption,
    )


def build_csr(key, cuit: str, organization: str, alias: str) -> x509.CertificateSigningRequest:
    """CSR con el DN que exige ARCA: C=AR, O=<razón social>, CN=<alias>, serialNumber=CUIT <n>."""
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "AR"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization),
            x509.NameAttribute(NameOID.COMMON_NAME, alias),
            x509.NameAttribute(NameOID.SERIAL_NUMBER, f"CUIT {normalize_cuit(cuit)}"),
        ]
    )
    return x509.CertificateSigningRequestBuilder().subject_name(subject).sign(key, hashes.SHA256())


def csr_pem(csr: x509.CertificateSigningRequest) -> bytes:
    return csr.public_bytes(serialization.Encoding.PEM)


def certificate_matches_key(cert_pem: bytes, key) -> bool:
    cert = x509.load_pem_x509_certificate(cert_pem)
    return cert.public_key().public_numbers() == key.public_key().public_numbers()


def write_private(path: Path, data: bytes) -> None:
    """Escribe un archivo legible solo por el dueño (0600)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def cuit_from_certificate(cert: x509.Certificate) -> str | None:
    """Extrae la CUIT del campo serialNumber del DN (``CUIT 20123456786``)."""
    for attr in cert.subject.get_attributes_for_oid(NameOID.SERIAL_NUMBER):
        match = re.fullmatch(r"CUIT\s*(\d{11})", str(attr.value).strip())
        if match:
            return match.group(1)
    return None
