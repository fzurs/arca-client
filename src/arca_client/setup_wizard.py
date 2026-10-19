"""Asistente interactivo para obtener un certificado de ARCA.

Genera la clave privada y el CSR, abre el sitio de ARCA y guía paso a paso
para crear el certificado y autorizarlo a un web service.

Uso:
    uv run arca-setup
"""

import getpass
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

from cryptography.hazmat.primitives import serialization

from .certificates import (
    build_csr,
    certificate_matches_key,
    csr_pem,
    generate_private_key,
    is_valid_alias,
    is_valid_cuit,
    normalize_cuit,
    private_key_pem,
    write_private,
)
from .environments import Environment

ARCA_LOGIN_URL = "https://auth.afip.gob.ar/contribuyente_/login.xhtml"
WSASS_MANUAL_URL = "https://www.arca.gob.ar/ws/WSASS/html/index.html"

_USE_COLOR = sys.stdout.isatty()


def _style(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def bold(text: str) -> str:
    return _style(text, "1")


def title(text: str) -> None:
    print()
    print(_style(f"━━ {text} ", "1;36") + _style("━" * max(0, 60 - len(text)), "36"))


def ok(text: str) -> None:
    print(_style("✓ ", "32") + text)


def warn(text: str) -> None:
    print(_style("! ", "33") + text)


def step(number: int, text: str) -> None:
    print(f"  {_style(f'{number}.', '1;35')} {text}")


def ask(prompt: str, default: str | None = None, validate=None, error: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{bold('?')} {prompt}{suffix}: ").strip()
        if not value and default is not None:
            value = default
        if value and (validate is None or validate(value)):
            return value
        warn(error or "Valor inválido, probá de nuevo.")


def confirm(prompt: str, default: bool = True) -> bool:
    hint = "S/n" if default else "s/N"
    while True:
        value = input(f"{bold('?')} {prompt} [{hint}]: ").strip().lower()
        if not value:
            return default
        if value in ("s", "si", "sí", "y", "yes"):
            return True
        if value in ("n", "no"):
            return False


def choose(prompt: str, options: list[tuple[str, str]], default: int = 1) -> str:
    print(f"{bold('?')} {prompt}")
    for i, (_, label) in enumerate(options, 1):
        print(f"  {i}) {label}")
    index = ask(
        "Elegí una opción",
        str(default),
        lambda v: v.isdigit() and 1 <= int(v) <= len(options),
    )
    return options[int(index) - 1][0]


def pause(prompt: str = "Presioná Enter cuando hayas terminado este paso") -> None:
    input(_style(f"  ↵ {prompt}...", "2"))


def copy_to_clipboard(text: str) -> bool:
    for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-ib"], ["pbcopy"]):
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, input=text.encode(), check=True, timeout=5)
                return True
            except (subprocess.SubprocessError, OSError):
                continue
    return False


def open_url(url: str) -> None:
    print(f"  {_style(url, '4;34')}")
    if confirm("¿Abrir esta página en el navegador?"):
        if not webbrowser.open(url):
            warn("No se pudo abrir el navegador; copiá el link de arriba.")


# --- Paso 1: datos y generación de archivos ---------------------------------


def collect_data() -> dict:
    title("Datos del certificado")
    env = choose(
        "¿Para qué ambiente es el certificado?",
        [
            (Environment.HOMOLOGACION.value, "Homologación (testing, vía WSASS)"),
            (Environment.PRODUCCION.value, "Producción"),
        ],
    )
    cuit = normalize_cuit(
        ask(
            "CUIT (11 dígitos, con o sin guiones)",
            validate=is_valid_cuit,
            error="CUIT inválida (revisá los 11 dígitos y el dígito verificador).",
        )
    )
    organization = ask("Razón social / nombre (campo O del certificado)")
    alias = ask(
        "Alias del certificado (solo letras y números, ej. 'miapp')",
        validate=is_valid_alias,
        error="Solo se permiten letras y números, sin espacios.",
    )
    out_dir = Path(ask("Carpeta donde guardar los archivos", f"certs/{env}")).expanduser()
    return {
        "env": Environment(env),
        "cuit": cuit,
        "organization": organization,
        "alias": alias,
        "out_dir": out_dir,
    }


def generate_files(data: dict) -> dict:
    out_dir: Path = data["out_dir"]
    alias = data["alias"]
    key_path = out_dir / f"{alias}.key"
    csr_path = out_dir / f"{alias}.csr"

    title("Clave privada y CSR")
    password = None
    if key_path.exists():
        warn(f"Ya existe {key_path}.")
        if not confirm("¿Reutilizar esa clave para generar el CSR? (no se sobrescribe)"):
            print("Elegí otro alias o carpeta y volvé a ejecutar el asistente.")
            sys.exit(1)
        if confirm("¿La clave tiene contraseña?", default=False):
            password = getpass.getpass("  Contraseña de la clave: ").encode()
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=password)
        ok(f"Usando la clave existente {key_path}")
    else:
        if confirm("¿Proteger la clave privada con contraseña?", default=False):
            while True:
                first = getpass.getpass("  Contraseña: ")
                if first and first == getpass.getpass("  Repetila: "):
                    password = first.encode()
                    break
                warn("Las contraseñas no coinciden o están vacías.")
        key = generate_private_key()
        write_private(key_path, private_key_pem(key, password))
        ok(f"Clave privada guardada en {bold(str(key_path))} (permisos 0600)")
        warn("Guardala bien: no se puede recuperar y sin ella el certificado no sirve.")

    csr = csr_pem(build_csr(key, data["cuit"], data["organization"], alias))
    out_dir.mkdir(parents=True, exist_ok=True)
    csr_path.write_bytes(csr)
    ok(f"CSR guardado en {bold(str(csr_path))}")
    return {**data, "key": key, "key_path": key_path, "csr_path": csr_path, "csr": csr.decode()}


# --- Paso 2: guía en el sitio de ARCA ----------------------------------------


def show_csr(csr: str) -> None:
    print()
    print(csr.rstrip())
    print()
    if copy_to_clipboard(csr):
        ok("El CSR también quedó copiado al portapapeles.")


def guide_login() -> None:
    title("Ingresar a ARCA")
    print("  Iniciá sesión con tu CUIT y clave fiscal (nivel 3 o superior).")
    open_url(ARCA_LOGIN_URL)
    pause("Presioná Enter cuando hayas iniciado sesión")


def guide_homologacion(ctx: dict) -> None:
    title("Habilitar WSASS (solo la primera vez)")
    print(
        "  Si en tu escritorio de ARCA ya ves «WSASS - Autogestión Certificados"
        " Homologación», salteá este paso."
    )
    step(1, "Entrá a «Administrador de Relaciones de Clave Fiscal».")
    step(2, "Hacé clic en «Adherir servicio».")
    step(
        3, "Elegí «ARCA › Servicios interactivos › WSASS - Autogestión Certificados Homologación»."
    )
    step(4, "Confirmá. WSASS va a aparecer en tu escritorio (a veces hay que volver a loguearse).")
    print("  Nota: WSASS no es delegable; hay que entrar con la clave fiscal de la persona física.")
    pause()

    title("Crear el certificado en WSASS")
    step(1, "Abrí «WSASS - Autogestión Certificados Homologación» desde el escritorio de ARCA.")
    step(2, "En el menú, hacé clic en «Nuevo Certificado».")
    step(3, f"En «1. Nombre simbólico del DN» escribí: {bold(ctx['alias'])}")
    step(4, "En «2. Solicitud del certificado en formato PKCS#10» pegá este CSR completo:")
    show_csr(ctx["csr"])
    step(5, "Hacé clic en «Crear DN y obtener certificado».")
    step(6, "Debajo aparece el certificado (-----BEGIN CERTIFICATE----- ...). Copialo.")
    print(f"  ¿Dudas? Manual de WSASS: {WSASS_MANUAL_URL}")
    obtain_certificate(ctx)

    title("Autorizar el certificado a un web service")
    service = ask("Web service a autorizar", ctx.get("service", "wsfe"))
    ctx["service"] = service
    step(1, "En WSASS, hacé clic en «Crear autorización a servicio».")
    step(2, f"En «1. Nombre simbólico del DN a autorizar» elegí: {bold(ctx['alias'])}")
    step(3, f"En «3. CUIT representado» poné: {bold(ctx['cuit'])}")
    step(4, f"En «5. Servicio al que desea acceder» elegí: {bold(service)}")
    step(5, "Hacé clic en «Crear autorización de acceso».")
    step(6, "Verificá en «Autorizaciones» que figure la nueva autorización.")
    pause()


def guide_produccion(ctx: dict) -> None:
    title("Habilitar «Administración de Certificados Digitales» (solo la primera vez)")
    print("  Si ya lo ves en tu escritorio de ARCA, salteá este paso.")
    step(1, "Entrá a «Administrador de Relaciones de Clave Fiscal».")
    step(2, "Hacé clic en «Adherir servicio».")
    step(3, "Elegí «ARCA › Servicios interactivos › Administración de Certificados Digitales».")
    step(4, "Confirmá.")
    pause()

    title("Crear el certificado")
    step(1, "Abrí «Administración de Certificados Digitales».")
    step(2, "Si administrás varios contribuyentes, elegí el que corresponde.")
    step(3, "Hacé clic en «Agregar alias».")
    step(4, f"En «Alias» escribí: {bold(ctx['alias'])}")
    step(5, f"En «Examinar» seleccioná el archivo: {bold(str(ctx['csr_path'].resolve()))}")
    step(6, "Hacé clic en «Agregar Alias».")
    step(7, "En la lista de alias, hacé clic en «Ver» y después en «Descargar».")
    obtain_certificate(ctx)

    title("Autorizar el certificado a un web service")
    service = ask("Web service a autorizar (ej. 'wsfe' = Facturación Electrónica)", "wsfe")
    ctx["service"] = service
    step(1, "Volvé al escritorio y abrí «Administrador de Relaciones de Clave Fiscal».")
    step(2, "Si administrás varios contribuyentes, elegí el que corresponde.")
    step(3, "Hacé clic en «Nueva Relación».")
    step(4, f"En «Representado» dejá la CUIT {bold(ctx['cuit'])} y hacé clic en «Buscar».")
    step(5, f"Abrí «ARCA › Web Services» y elegí el servicio correspondiente a {bold(service)}.")
    step(6, "Hacé clic en el segundo «Buscar» (el del representante).")
    step(7, f"Elegí el certificado {bold(ctx['alias'])} y hacé clic en «Confirmar».")
    step(8, "Hacé clic en «Confirmar» de nuevo.")
    pause()


def read_certificate_input() -> bytes:
    """Lee el certificado pegado en la terminal o desde una ruta de archivo."""
    print(
        f"{bold('?')} Pegá el certificado (desde -----BEGIN CERTIFICATE----- hasta"
        " -----END CERTIFICATE-----) o escribí la ruta del archivo descargado:"
    )
    first = input("  ").strip()
    if "BEGIN CERTIFICATE" not in first:
        path = Path(first.strip("'\"")).expanduser()
        if not path.is_file():
            raise ValueError(f"No existe el archivo {path}")
        return path.read_bytes()
    lines = [first]
    while "END CERTIFICATE" not in lines[-1]:
        lines.append(input().strip())
    return ("\n".join(lines) + "\n").encode()


def obtain_certificate(ctx: dict) -> None:
    cert_path = ctx["out_dir"] / f"{ctx['alias']}.crt"
    while True:
        try:
            cert = read_certificate_input()
            if certificate_matches_key(cert, ctx["key"]):
                break
            warn(
                "Ese certificado no corresponde a la clave privada generada. ¿Copiaste el correcto?"
            )
        except ValueError as e:
            warn(f"No pude leer el certificado: {e}")
    cert_path.write_bytes(cert)
    ctx["cert_path"] = cert_path
    ok(f"Certificado verificado y guardado en {bold(str(cert_path))}")


# --- Paso 3: prueba ----------------------------------------------------------


def try_login(ctx: dict) -> None:
    title("Probar la autenticación")
    if not confirm(f"¿Pedir un Ticket de Acceso para '{ctx['service']}' ahora?"):
        return
    from .exceptions import ArcaError
    from .wsaa import WSAA

    try:
        password = None
        if ctx.get("key_encrypted"):
            password = getpass.getpass("  Contraseña de la clave: ").encode()
        wsaa = WSAA(ctx["cert_path"], ctx["key_path"], ctx["env"], key_password=password)
        ticket = wsaa.get_ticket(ctx["service"])
    except ArcaError as e:
        warn(f"El WSAA respondió con un error: {e}")
        print(
            "  Si recién creaste la autorización, esperá unos minutos"
            " y probá con examples/login.py."
        )
        return
    except Exception as e:  # red, TLS, WSDL, etc.
        warn(f"No se pudo conectar con el WSAA: {e}")
        return
    ok(f"¡Autenticación exitosa! El ticket vence el {ticket.expiration_time}.")


def summary(ctx: dict) -> None:
    title("Listo")
    print(f"  Clave privada: {ctx['key_path']}")
    print(f"  Certificado:   {ctx['cert_path']}")
    print(f"  CSR:           {ctx['csr_path']} (ya no hace falta, lo podés borrar)")
    print()
    print("  Para usarlo:")
    print(f"    ARCA_ENV={ctx['env'].value} ARCA_CERT={ctx['cert_path']} \\")
    print(f"    ARCA_KEY={ctx['key_path']} uv run python examples/login.py {ctx['service']}")
    warn("No subas la clave privada ni el certificado al repositorio.")


def main() -> None:
    print(bold("Asistente de certificados de ARCA"))
    print("Genera la clave privada y el CSR, y te guía para obtener y autorizar el certificado.")
    try:
        ctx = generate_files(collect_data())
        ctx["key_encrypted"] = b"ENCRYPTED" in ctx["key_path"].read_bytes()
        guide_login()
        if ctx["env"] is Environment.HOMOLOGACION:
            guide_homologacion(ctx)
        else:
            guide_produccion(ctx)
        try_login(ctx)
        summary(ctx)
    except (KeyboardInterrupt, EOFError):
        print()
        warn("Asistente cancelado. Los archivos generados hasta ahora quedaron en disco.")
        sys.exit(130)


if __name__ == "__main__":
    main()
