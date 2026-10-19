# arca-client

Cliente Python para los web services SOAP de **ARCA** (ex AFIP).

Estado actual: **autenticación (WSAA)**. Los servicios de negocio (WSFE, padrón, etc.) vendrán después.

## Instalación

```bash
uv sync
```

## Requisitos previos: certificado

Para usar cualquier web service de ARCA se necesita un certificado X.509 emitido por ARCA, asociado al servicio que se quiere consumir.

1. Generar la clave privada y el CSR:
   ```bash
   openssl genrsa -out private.key 2048
   openssl req -new -key private.key -out pedido.csr \
     -subj "/C=AR/O=Mi Empresa/CN=mi-sistema/serialNumber=CUIT 20123456780"
   ```
2. **Homologación**: con clave fiscal, entrar a *WSASS - Autogestión Certificados Homologación*, pegar el CSR y descargar el certificado. Después, en WSASS, crear la autorización del certificado para el servicio (ej. `wsfe`).
3. **Producción**: usar *Administrador de Certificados Digitales* para emitir el certificado y *Administrador de Relaciones de Clave Fiscal* para asociarlo al servicio.

Documentación oficial: <https://www.arca.gob.ar/ws/documentacion/wsaa.asp>

## Uso

```python
from arca_client import WSAA, Environment

wsaa = WSAA("cert.crt", "private.key", Environment.HOMOLOGACION)
ticket = wsaa.get_ticket("wsfe")

ticket.token, ticket.sign, ticket.expiration_time
```

O con el ejemplo incluido:

```bash
ARCA_CERT=cert.crt ARCA_KEY=private.key uv run python examples/login.py wsfe
```

### Caché de tickets

Un Ticket de Acceso (TA) dura **12 horas**, y el WSAA rechaza pedir uno nuevo mientras siga vigente el anterior (`coe.alreadyAuthenticated`). Por eso `get_ticket()` guarda el TA en `.ta_cache/` (con permisos `0600`) y lo reutiliza hasta 5 minutos antes de que venza.

Se puede usar otro backend con cualquier objeto que tenga `get(key)` y `set(key, ticket)`, por ejemplo `MemoryTicketCache()` o uno propio sobre Redis:

```python
from arca_client import MemoryTicketCache

wsaa = WSAA(cert, key, cache=MemoryTicketCache())
```

### Errores

Los SoapFault del WSAA se convierten en `WSAAError`, con `code` (ej. `cms.cert.expired`) y `retryable`. Según la especificación, solo se puede reintentar ante `wsaa.*` y `wsn.unavailable`.

## Desarrollo

```bash
uv run pytest
uv run ruff check .
```

## Licencia

MIT
