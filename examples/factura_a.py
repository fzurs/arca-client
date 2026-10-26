"""Emite una Factura A (IVA 21 %) a un responsable inscripto en homologación y genera su PDF.

Uso:
    ARCA_CERT=cert.crt ARCA_KEY=private.key \
    uv run python examples/factura_a.py <cuit_receptor> [neto] [pto_vta]

Los datos del emisor para el PDF se leen de ARCA_RAZON_SOCIAL, ARCA_DOMICILIO,
ARCA_INICIO_ACTIVIDADES (AAAA-MM-DD) y ARCA_IIBB; si faltan se usan valores de ejemplo.
"""

import os
import sys
from datetime import date
from pathlib import Path

from arca_client import (
    WSAA,
    WSFE,
    Alicuota,
    CondicionIVAReceptor,
    Emisor,
    Environment,
    Item,
    WSFEError,
    neto_por_alicuota,
    render_invoice_pdf,
)

if len(sys.argv) < 2:
    sys.exit(__doc__)
cuit_receptor = int(sys.argv[1].replace("-", ""))
neto = sys.argv[2] if len(sys.argv) > 2 else "1000.00"
punto_venta = int(sys.argv[3]) if len(sys.argv) > 3 else 1
env = Environment(os.environ.get("ARCA_ENV", "homologacion"))

# TODO: soportar claves con contraseña (ver "Limitaciones conocidas" en el README).
wsaa = WSAA(os.environ["ARCA_CERT"], os.environ["ARCA_KEY"], env)
wsfe = WSFE(wsaa)

items = [Item("Venta de prueba", neto, alicuota=Alicuota.IVA_21)]
try:
    factura = wsfe.create_invoice_a(
        punto_venta=punto_venta,
        doc_nro=cuit_receptor,
        condicion_iva_receptor=CondicionIVAReceptor.IVA_RESPONSABLE_INSCRIPTO,
        neto=neto_por_alicuota(items),
    )
except WSFEError as e:
    print(f"Rechazada: {e}")
    sys.exit(1)

print(f"Factura A {factura.punto_venta:05d}-{factura.numero:08d} ({env.value})")
print(f"Fecha:        {factura.fecha}")
print(f"Neto:         $ {factura.importe_neto}")
for alicuota in factura.iva:
    print(f"IVA {alicuota.alicuota.porcentaje}%:      $ {alicuota.importe}")
print(f"Total:        $ {factura.importe_total}")
print(f"CAE:          {factura.cae}")
print(f"Vence CAE:    {factura.cae_vencimiento}")
for obs in factura.observaciones:
    print(f"Observación:  {obs}")

emisor = Emisor(
    razon_social=os.environ.get("ARCA_RAZON_SOCIAL", "Emisor de Prueba"),
    domicilio=os.environ.get("ARCA_DOMICILIO", "Calle Falsa 123, CABA"),
    inicio_actividades=date.fromisoformat(os.environ.get("ARCA_INICIO_ACTIVIDADES", "2020-01-01")),
    ingresos_brutos=os.environ.get("ARCA_IIBB", "Exento"),
)
pdf = render_invoice_pdf(factura, emisor, items)
path = Path(f"factura_A_{factura.punto_venta:05d}-{factura.numero:08d}.pdf")
path.write_bytes(pdf)
print(f"PDF:          {path.resolve()}")
