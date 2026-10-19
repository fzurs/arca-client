# arca-client

Cliente Python para los web services SOAP de **ARCA** (ex AFIP).

Estado actual: **autenticación (WSAA)**. Los servicios de negocio (WSFE, padrón, etc.) vendrán después.

## Instalación

```bash
uv sync
```

## Requisitos previos: certificado

Para usar cualquier web service de ARCA se necesita un certificado X.509 emitido por ARCA, asociado al servicio que se quiere consumir.

### Asistente interactivo (recomendado)

```bash
uv run arca-setup
```

El asistente:

1. Pide el ambiente, la CUIT (valida el dígito verificador), la razón social y el alias.
2. Genera la clave privada (`<alias>.key`, permisos 0600, contraseña opcional) y el CSR (`<alias>.csr`) en `certs/<ambiente>/`.
3. Abre el login de ARCA en el navegador y explica paso a paso dónde pegar el CSR: WSASS en homologación, *Administración de Certificados Digitales* en producción.
4. Pide que pegues el certificado emitido (o su ruta), verifica que corresponda a la clave y lo guarda como `<alias>.crt`.
5. Explica cómo autorizar el certificado a un web service: WSASS en homologación, *Administrador de Relaciones de Clave Fiscal* en producción.
6. Opcionalmente prueba un login real contra el WSAA.

### Manual con OpenSSL

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

Un Ticket de Acceso (TA) dura **12 horas**, y el WSAA rechaza pedir uno nuevo mientras siga vigente el anterior (`coe.alreadyAuthenticated`). Por eso `get_ticket()` guarda el TA en disco (con permisos `0600`) y lo reutiliza hasta 5 minutos antes de que venza.

La ubicación es fija, sin importar desde qué directorio se ejecute:

1. `$ARCA_TA_CACHE`, si está definida.
2. `$XDG_CACHE_HOME/arca-client/tickets/`.
3. `~/.cache/arca-client/tickets/` (valor por defecto).

Cada archivo se llama `<ambiente>-<servicio>-<huella del certificado>.json`. **No borres esta carpeta mientras haya tickets vigentes**: si se pierde el TA, ARCA no deja pedir otro hasta que venza (hasta 12 h).

Se puede usar otro backend con cualquier objeto que tenga `get(key)` y `set(key, ticket)`, por ejemplo `MemoryTicketCache()` o uno propio sobre Redis:

```python
from arca_client import MemoryTicketCache

wsaa = WSAA(cert, key, cache=MemoryTicketCache())
```

`MemoryTicketCache` pierde el TA cuando termina el proceso, así que solo conviene para tests o para un proceso que corre de forma permanente.

### Errores

Los SoapFault del WSAA se convierten en `WSAAError`, con `code` (ej. `cms.cert.expired`) y `retryable`. Según la especificación, solo se puede reintentar ante `wsaa.*` y `wsn.unavailable`.

## Limitaciones conocidas

- **`examples/login.py` no admite claves privadas con contraseña.** Si la clave se generó con contraseña (por ejemplo, eligiendo esa opción en `arca-setup`), el script falla al cargarla. La librería sí lo soporta con `WSAA(..., key_password=b"...")`; lo que falta es exponerlo en el ejemplo, ya sea con una variable `ARCA_KEY_PASSWORD` o pidiéndola por teclado.

## Desarrollo

```bash
uv run pytest
uv run ruff check .
```

## Licencia

MIT
