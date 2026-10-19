import base64

from cryptography.hazmat.primitives.serialization import pkcs7

from arca_client.wsaa.signer import load_certificate, load_private_key, sign_tra
from arca_client.wsaa.tra import build_tra


def test_sign_tra_embeds_content_and_certificate(cert_pem, key_pem):
    cert = load_certificate(cert_pem)
    tra = build_tra("wsfe")
    der = base64.b64decode(sign_tra(tra, cert, load_private_key(key_pem)))

    assert tra in der  # contenido adjunto, no detached
    assert pkcs7.load_der_pkcs7_certificates(der) == [cert]
