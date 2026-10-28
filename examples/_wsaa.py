"""WSAA de los ejemplos, armado con variables de entorno.

- ``ARCA_CERT`` y ``ARCA_KEY``: rutas del certificado y la clave privada.
- ``ARCA_ENV``: ``homologacion`` (por defecto) o ``produccion``.
- ``ARCA_KEY_PASSWORD``: contraseña de la clave, si tiene. Si la clave está cifrada y
  no está definida, se pide por teclado.
"""

import os
import sys
from getpass import getpass
from pathlib import Path

from arca_client import WSAA, Environment


def wsaa_desde_entorno() -> WSAA:
    env = Environment(os.environ.get("ARCA_ENV", "homologacion"))
    key = os.environ["ARCA_KEY"]
    password = os.environ.get("ARCA_KEY_PASSWORD")
    # PKCS#8 ("ENCRYPTED PRIVATE KEY") y el formato tradicional ("Proc-Type: 4,ENCRYPTED").
    if password is None and b"ENCRYPTED" in Path(key).read_bytes():
        password = getpass(f"Contraseña de {key}: ")
    try:
        return WSAA(
            os.environ["ARCA_CERT"],
            key,
            env,
            key_password=password.encode() if password else None,
        )
    except (TypeError, ValueError) as e:
        sys.exit(f"No se pudo cargar la clave {key} (¿contraseña incorrecta?): {e}")
