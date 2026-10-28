"""Obtiene un Ticket de Acceso del WSAA de homologación.

Uso:
    ARCA_CERT=cert.crt ARCA_KEY=private.key uv run python examples/login.py [servicio]

Si la clave tiene contraseña, se toma de ARCA_KEY_PASSWORD o se pide por teclado.
"""

import sys

from _wsaa import wsaa_desde_entorno

service = sys.argv[1] if len(sys.argv) > 1 else "wsfe"
wsaa = wsaa_desde_entorno()
env = wsaa.env
ticket = wsaa.get_ticket(service)

print(f"Servicio:   {service} ({env.value})")
print(f"Generado:   {ticket.generation_time}")
print(f"Expira:     {ticket.expiration_time}")
print(f"Token:      {ticket.token[:40]}...")
print(f"Sign:       {ticket.sign[:40]}...")
print(f"Caché:      {wsaa.cache.directory}")
