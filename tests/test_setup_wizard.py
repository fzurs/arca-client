from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

from arca_client import certificate_paths, setup_wizard


def _sign_csr(csr_path, key_path):
    """Simula a WSASS: emite un certificado para el CSR generado."""
    csr = x509.load_pem_x509_csr(csr_path.read_bytes())
    key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(csr.subject)
        .issuer_name(csr.subject)
        .public_key(csr.public_key())
        .serial_number(1)
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def test_full_homologacion_flow(tmp_path, monkeypatch, capsys):
    base_dir = tmp_path / "certs"
    out_dir = base_dir / "homologacion"
    pasted: list[str] = []
    opened = []

    def fake_input(prompt=""):
        if pasted:
            return pasted.pop(0)
        if "ambiente" in prompt or "Elegí una opción" in prompt:
            return "1"
        if "CUIT" in prompt:
            return "20-12345678-6"
        if "Razón social" in prompt:
            return "Empresa de Prueba"
        if "Alias" in prompt:
            return "miapp"
        if "Carpeta" in prompt:
            return str(base_dir)
        if "contraseña" in prompt:
            return "n"
        if "navegador" in prompt:
            return "s"
        if "Web service" in prompt:
            return "wsfe"
        if "Ticket de Acceso" in prompt:
            return "n"
        if prompt.startswith("  ") and "↵" not in prompt and "Presioná" not in prompt:
            # Pedido del certificado: "pegarlo" línea por línea.
            lines = _sign_csr(out_dir / "miapp.csr", out_dir / "miapp.key").splitlines()
            pasted.extend(lines[1:])
            return lines[0]
        return ""

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(setup_wizard.webbrowser, "open", lambda url: opened.append(url) or True)
    monkeypatch.setattr(setup_wizard, "copy_to_clipboard", lambda text: False)

    setup_wizard.main()

    assert (out_dir / "miapp.key").exists()
    assert (out_dir / "miapp.csr").exists()
    assert b"BEGIN CERTIFICATE" in (out_dir / "miapp.crt").read_bytes()
    # Mismas rutas que usa WSAA.from_alias.
    assert certificate_paths("miapp", "homologacion", base_dir) == (
        out_dir / "miapp.crt",
        out_dir / "miapp.key",
    )
    assert opened == [setup_wizard.ARCA_LOGIN_URL]
    output = capsys.readouterr().out
    assert "Nuevo Certificado" in output
    assert "Crear autorización a servicio" in output
