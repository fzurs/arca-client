from arca_client.certificates import cuit_from_certificate
from arca_client.wsaa.signer import load_certificate


def test_cuit_from_certificate(cert_pem):
    assert cuit_from_certificate(load_certificate(cert_pem)) == "20123456780"
