"""Firma CMS (PKCS#7 SignedData) del TRA."""

import base64

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs7


def load_certificate(cert_pem: bytes) -> x509.Certificate:
    return x509.load_pem_x509_certificate(cert_pem)


def load_private_key(key_pem: bytes, password: bytes | None = None):
    return serialization.load_pem_private_key(key_pem, password=password)


def sign_tra(
    tra: bytes,
    certificate: x509.Certificate,
    private_key,
    hash_algorithm: hashes.HashAlgorithm | None = None,
) -> str:
    """Firma el TRA y devuelve el CMS en DER codificado en Base64.

    El contenido queda adjunto (no detached), como exige el WSAA. `Binary` evita
    que se conviertan los saltos de línea a CRLF (canonicalización S/MIME).
    """
    signed = (
        pkcs7.PKCS7SignatureBuilder()
        .set_data(tra)
        .add_signer(certificate, private_key, hash_algorithm or hashes.SHA256())
        .sign(serialization.Encoding.DER, options=[pkcs7.PKCS7Options.Binary])
    )
    return base64.b64encode(signed).decode("ascii")
