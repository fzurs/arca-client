"""Obtiene un Ticket de Acceso del WSAA de homologación.

Uso:
    ARCA_CERT=cert.crt ARCA_KEY=private.key uv run python examples/login.py [servicio]
"""

import os
import sys

from arca_client import WSAA, Environment

service = sys.argv[1] if len(sys.argv) > 1 else "wsfe"
env = Environment(os.environ.get("ARCA_ENV", "homologacion"))

wsaa = WSAA(os.environ["ARCA_CERT"], os.environ["ARCA_KEY"], env)
ticket = wsaa.get_ticket(service)

print(f"Servicio:   {service} ({env.value})")
print(f"Generado:   {ticket.generation_time}")
print(f"Expira:     {ticket.expiration_time}")
print(f"Token:      {ticket.token[:40]}...")
print(f"Sign:       {ticket.sign[:40]}...")
