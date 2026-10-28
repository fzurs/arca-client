"""Verifica en ARCA (WSCDC) un comprobante recibido, a partir del texto de su código QR.

Uso:
    ARCA_CERT=cert.crt ARCA_KEY=private.key \
    uv run python examples/verificar_comprobante.py "https://www.arca.gob.ar/fe/qr/?p=..."

El texto del QR se obtiene escaneándolo (por ejemplo, con la cámara del celular). El
certificado tiene que estar autorizado al servicio "wscdc". Si la clave tiene contraseña,
se toma de ARCA_KEY_PASSWORD o se pide por teclado.
"""

import sys

from _wsaa import wsaa_desde_entorno

from arca_client import WSCDC, WSAAError, WSCDCError, parse_qr_url

if len(sys.argv) < 2:
    sys.exit(__doc__)
url = sys.argv[1]

datos = parse_qr_url(url)
print(
    f"Comprobante: tipo {datos['tipoCmp']} {datos['ptoVta']:05d}-{datos['nroCmp']:08d} "
    f"de la CUIT {datos['cuit']}, del {datos['fecha']}, por {datos['moneda']} {datos['importe']}"
)

wscdc = WSCDC(wsaa_desde_entorno())
try:
    resultado = wscdc.verify_qr(url)
except WSAAError as e:
    sys.exit(f"Sin acceso a WSCDC (¿el certificado está autorizado a wscdc?): {e}")
except WSCDCError as e:
    sys.exit(f"ARCA no pudo verificarlo: {e}")

print("Resultado:   " + ("APROBADO" if resultado.aprobada else "NO COINCIDE CON ARCA"))
for mensaje in [*resultado.observaciones, *resultado.errores]:
    print(f"  {mensaje}")
sys.exit(0 if resultado.aprobada else 1)
