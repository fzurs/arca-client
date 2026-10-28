# arca-client

Cliente Python para los web services SOAP de **ARCA** (ex AFIP).

Estado actual: **autenticación (WSAA)**, **factura electrónica (WSFEv1)** y **constatación de comprobantes (WSCDC)**. Con WSFEv1 se emiten la Factura C y sus notas de crédito/débito para monotributistas, y la Factura A, la Factura B y sus notas de crédito/débito para responsables inscriptos. Con WSCDC se verifican las facturas recibidas de proveedores.

## Instalación

```bash
uv sync                 # desarrollo
pip install arca-client[pdf]   # como dependencia, con generación de PDF
```

## Requisitos previos: certificado

Para usar cualquier web service de ARCA se necesita un certificado X.509 emitido por ARCA, asociado al servicio que se quiere consumir.

### Asistente interactivo (recomendado)

```bash
uv run arca-setup
```

El asistente:

1. Pide el ambiente, la CUIT (valida el dígito verificador), la razón social y el alias.
2. Genera la clave privada (`<alias>.key`, permisos 0600, contraseña opcional) y el CSR (`<alias>.csr`) en `<carpeta base>/<ambiente>/` (por defecto `certs/homologacion/` o `certs/produccion/`).
3. Abre el login de ARCA en el navegador y explica paso a paso dónde pegar el CSR: WSASS en homologación, *Administración de Certificados Digitales* en producción.
4. Pide que pegues el certificado emitido (o su ruta), verifica que corresponda a la clave y lo guarda como `<alias>.crt`.
5. Explica cómo autorizar el certificado a un web service: WSASS en homologación, *Administrador de Relaciones de Clave Fiscal* en producción.
6. Opcionalmente prueba un login real contra el WSAA.

Los archivos quedan en `certs/<ambiente>/<alias>.crt|.key`, y `WSAA.from_alias` los carga desde ahí:

```python
from arca_client import WSAA, Environment, certificate_paths

wsaa = WSAA.from_alias("miapp", Environment.HOMOLOGACION)
# Equivale a WSAA("certs/homologacion/miapp.crt", "certs/homologacion/miapp.key", ...)

cert_path, key_path = certificate_paths("miapp", "produccion", base_dir="/srv/app/certs")
```

`base_dir` (por defecto `"certs"`) se resuelve contra el directorio actual: una aplicación conviene que pase una ruta absoluta, por ejemplo `WSAA.from_alias("miapp", env, base_dir=BASE_DIR / "certs")`. El resto de los argumentos (`key_password`, `cache`, `timeout`, …) van igual que en `WSAA(...)`. Si falta el `.crt` o el `.key`, lanza `ArcaError` con la ruta buscada.

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

Todos los ejemplos leen el ambiente de `ARCA_ENV` (`homologacion` por defecto). Si la clave privada tiene contraseña, la toman de `ARCA_KEY_PASSWORD` o, si no está definida, la piden por teclado.

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

Si varios procesos piden el TA a la vez, `FileTicketCache` los serializa con un archivo de bloqueo (`<clave>.lock`): solo uno llama al WSAA y los demás leen el TA del caché. Un backend propio puede hacer lo mismo con un método `lock(key)` que devuelva un context manager (por ejemplo, un lock de Redis); si no lo tiene, ante `coe.alreadyAuthenticated` se relee el caché una vez antes de fallar. El bloqueo de archivo solo coordina procesos de la misma máquina.

## Factura electrónica (WSFEv1)

| Método | Comprobante | Emisor | Receptor |
|---|---|---|---|
| `create_invoice_c` | Factura C (11) | Monotributista | Cualquiera |
| `create_invoice_a` | Factura A (1) | Responsable inscripto | Responsable inscripto o monotributista |
| `create_invoice_b` | Factura B (6) | Responsable inscripto | Consumidor final, exento, no categorizado, etc. |
| `create_credit_note_a` / `create_debit_note_a` | Nota de Crédito A (3) / Nota de Débito A (2) | Responsable inscripto | Como la Factura A |
| `create_credit_note_b` / `create_debit_note_b` | Nota de Crédito B (8) / Nota de Débito B (7) | Responsable inscripto | Como la Factura B |
| `create_credit_note_c` / `create_debit_note_c` | Nota de Crédito C (13) / Nota de Débito C (12) | Monotributista | Como la Factura C |

### Factura C (monotributistas)

```python
from datetime import date
from arca_client import WSAA, WSFE, Concepto, CondicionIVAReceptor, DocTipo, Environment

wsaa = WSAA("cert.crt", "private.key", Environment.HOMOLOGACION)
wsfe = WSFE(wsaa)  # la CUIT emisora se toma del certificado (o pasala con cuit=...)

# Consumidor final sin identificar, venta de productos
factura = wsfe.create_invoice_c(punto_venta=1, importe_total="1500.00")
print(factura.numero, factura.cae, factura.cae_vencimiento)

# Servicios a un receptor identificado
wsfe.create_invoice_c(
    punto_venta=1,
    importe_total="25000.00",
    concepto=Concepto.SERVICIOS,
    doc_tipo=DocTipo.CUIT,
    doc_nro=30712345671,
    condicion_iva_receptor=CondicionIVAReceptor.IVA_RESPONSABLE_INSCRIPTO,
    servicio_desde=date(2026, 10, 1),
    servicio_hasta=date(2026, 10, 31),
)
```

Para leer la CUIT del certificado sin crear un `WSFE` (por ejemplo, para el PDF), `cuit_from_certificate` acepta las mismas entradas que `WSAA`: una ruta, el PEM en bytes o un `x509.Certificate`. Devuelve `None` si el certificado no trae CUIT.

```python
from arca_client import cuit_from_certificate

cuit_from_certificate("certs/homologacion/miapp.crt")  # "20123456786"
```

`create_invoice_c`:

- Consulta el último número autorizado (`FECompUltimoAutorizado`) y usa el siguiente.
- Si otro proceso usa ese número antes (rechazo 10016 y el último autorizado avanzó), reintenta con el siguiente, hasta `numbering_attempts` veces (3 por defecto). Es seguro: un comprobante rechazado no se emite ni consume número. Si el último no avanzó, el 10016 es por otra causa (por ejemplo, una fecha anterior a la del último comprobante) y se informa sin reintentar.
- Arma el comprobante C: `ImpNeto` = `ImpTotal`, IVA, exento y no gravado en 0, sin array de IVA, moneda `PES`.
- Informa la condición frente al IVA del receptor (obligatoria, RG 5616). Para consumidor final se completa sola; con un receptor identificado hay que indicarla.
- Valida localmente lo básico: importe positivo con hasta 2 decimales (como `Decimal`, `int` o `str`, nunca `float`), documento y período de servicio.
- Devuelve un `ComprobanteAutorizado` con el CAE y su vencimiento. Si el comprobante se rechaza, lanza `WSFEError` con los `errores` y `observaciones` de ARCA (los códigos están en `e.codes`).

Ejemplo de línea de comandos:

```bash
ARCA_CERT=cert.crt ARCA_KEY=private.key uv run python examples/factura_c.py 1500.00 1
```

### Responsable inscripto: Factura A y B

Los importes se pasan como **neto por alícuota**; la librería calcula el IVA de cada alícuota sobre su base (redondeo half even, criterio de ARCA) y el total.

```python
from arca_client import Alicuota, CondicionIVAReceptor, TipoTributo, Tributo

factura = wsfe.create_invoice_a(
    punto_venta=1,
    doc_nro=30712345671,  # CUIT del receptor (obligatoria en la A)
    condicion_iva_receptor=CondicionIVAReceptor.IVA_RESPONSABLE_INSCRIPTO,
    neto={Alicuota.IVA_21: "1000.00", Alicuota.IVA_10_5: "500.00"},
    exento="100.00",  # opcionales: exento= y no_gravado=
    tributos=[Tributo(TipoTributo.PERCEPCION_IIBB, base_imponible="1500.00", alicuota="3")],
)
factura.importe_neto, factura.importe_iva, factura.importe_tributos, factura.importe_total
# Decimal('1500.00'), Decimal('262.50'), Decimal('45.00'), Decimal('1907.50')
factura.iva  # [IvaAlicuota(IVA_10_5, 500.00, 52.50), IvaAlicuota(IVA_21, 1000.00, 210.00)]

# Factura B a consumidor final sin identificar (también admite receptor identificado)
wsfe.create_invoice_b(punto_venta=1, neto={Alicuota.IVA_21: "1000.00"})
```

- `ImpTotal = neto + no gravado + exento + IVA + tributos`. Si hay neto gravado se envía el array `Iva`; si todo es exento o no gravado, no.
- La B también informa el IVA discriminado a ARCA (así lo pide WSFEv1); lo que cambia es el PDF, que muestra los precios con IVA incluido.
- La condición frente al IVA del receptor se valida localmente contra la clase: la A es para responsables inscriptos y monotributistas (RG 5003), la B para el resto (`CONDICIONES_A` / `CONDICIONES_B`). La CUIT/CUIL del receptor se valida con su dígito verificador.
- Alícuotas: `IVA_0`, `IVA_2_5`, `IVA_5`, `IVA_10_5`, `IVA_21`, `IVA_27`.
- `Tributo(tipo, base_imponible, alicuota, importe=None, descripcion="")`: si no se indica `importe` se calcula `base × alícuota / 100`. Sirve para percepciones de IVA o IIBB, impuestos internos, etc., y también se puede usar en la Factura C (`create_invoice_c(..., tributos=[...])`, donde el neto pasa a ser el total menos los tributos).
- El tope a partir del cual hay que identificar al consumidor final en la B lo valida ARCA.
- La recuperación ante cortes, el reintento de numeración y `get_voucher` funcionan igual para todos los tipos; `get_voucher` devuelve también el detalle de IVA, tributos y comprobantes asociados.

Ejemplo de línea de comandos (Factura A al 21 %):

```bash
ARCA_CERT=cert.crt ARCA_KEY=private.key uv run python examples/factura_a.py 30712345671 1000.00 1
```

### Notas de crédito y débito

Cada nota lleva los mismos argumentos que la factura de su clase (`create_invoice_a`, `create_invoice_b` o `create_invoice_c`), más el comprobante al que se refiere (`asociado=`) o, si abarca varios, el período (`periodo=(desde, hasta)`). Hay que indicar uno de los dos, y el asociado tiene que ser de la misma letra que la nota: una nota B solo puede referirse a un comprobante B.

```python
nota = wsfe.create_credit_note_a(
    punto_venta=1,
    asociado=factura,  # un ComprobanteAutorizado o ComprobanteAsociado(tipo, punto_venta, numero)
    doc_nro=30712345671,
    condicion_iva_receptor=CondicionIVAReceptor.IVA_RESPONSABLE_INSCRIPTO,
    neto={Alicuota.IVA_21: "100.00"},
)

# B: neto por alícuota; consumidor final sin identificar por defecto, como la Factura B
wsfe.create_credit_note_b(punto_venta=1, asociado=factura_b, neto={Alicuota.IVA_21: "1000.00"})

# C: importe total, como la Factura C
wsfe.create_credit_note_c(1, "500.00", asociado=factura_c)
wsfe.create_debit_note_c(1, "100.00", periodo=(date(2026, 9, 1), date(2026, 9, 30)))
```

### Consultar parámetros de ARCA

Consultas seguras (no emiten nada), útiles para verificar códigos:

```python
wsfe.condiciones_iva_receptor(
    "A"
)  # {1: 'IVA Responsable Inscripto', 6: 'Responsable Monotributo', ...}
wsfe.tipos_iva()  # {3: '0%', 4: '10.5%', 5: '21%', ...}
wsfe.tipos_tributo()  # {1: 'Impuestos nacionales', ..., 7: 'Percepción de IIBB', ...}
```

### Cortes de comunicación

Si se corta la conexión después de enviar `FECAESolicitar` (timeout, error de red, respuesta ilegible), no se sabe si ARCA autorizó el comprobante. Reintentar con `create_invoice_c` calcularía el **número siguiente** y, si el primero se había autorizado, emitiría una factura duplicada.

Por eso, ante un corte, `create_invoice_c` consulta ese mismo número con `FECompConsultar`, como recomienda el manual de ARCA ("Operatoria con errores de comunicación"):

| Resultado de la consulta | Qué pasa |
|---|---|
| El comprobante existe con los datos enviados | Devuelve el `ComprobanteAutorizado` con su CAE, como si no hubiera habido corte. |
| ARCA no lo registra (error 602) | Lanza `WSFEConnectionError` con `safe_to_retry=True`: se puede reintentar. |
| La consulta también falla (3 intentos) | Lanza `WSFEConnectionError` con `safe_to_retry=False`: **no reintentar**. Verificá con `wsfe.get_voucher(e.punto_venta, e.tipo, e.numero)` cuando vuelva la conexión. |
| Existe pero con otros datos | Lanza `WSFEConnectionError` con `safe_to_retry=False` (probablemente otro proceso usó el número). |

```python
from arca_client import WSFEConnectionError

try:
    factura = wsfe.create_invoice_c(punto_venta=1, importe_total="1500.00")
except WSFEConnectionError as e:
    if e.safe_to_retry:
        factura = wsfe.create_invoice_c(punto_venta=1, importe_total="1500.00")
    else:
        # Guardar e.punto_venta / e.numero y verificar más tarde con get_voucher
        raise
```

Lo mismo vale para `create_invoice_a`, `create_invoice_b` y las notas de crédito/débito.

`wsfe.get_voucher(punto_venta, tipo, numero)` también sirve por sí solo para consultar cualquier comprobante emitido; devuelve `None` si no existe. Todas las llamadas a ARCA tienen timeout (30 s por defecto, configurable con `WSFE(..., timeout=...)`).

### PDF con código QR

ARCA no ofrece un servicio para generar el PDF de las facturas emitidas por web service: lo tiene que generar el emisor. `render_invoice_pdf` arma la representación impresa de cualquiera de los comprobantes anteriores con el código QR obligatorio (RG 4892). Requiere el extra `pdf` (`fpdf2` y `segno`).

```python
from datetime import date
from pathlib import Path
from arca_client import Emisor, Item, Receptor, render_invoice_pdf

emisor = Emisor(
    razon_social="Juan Pérez",
    domicilio="Av. Corrientes 1234, CABA",
    inicio_actividades=date(2020, 3, 1),
    ingresos_brutos="20-12345678-6",
)
items = [
    Item("Diseño de logotipo", "18000.00", codigo="D01"),
    Item("Horas de soporte", "2550.00", cantidad=3, unidad="horas", bonificacion="10"),
]
pdf = render_invoice_pdf(factura, emisor, items, Receptor("Empresa S.A.", "San Martín 500"))
Path("factura.pdf").write_bytes(pdf)
```

- La suma de los ítems tiene que coincidir con el importe autorizado; si no, se lanza `ValueError`. Para no calcularlo a mano, autorizá con `importe_total=sum(i.subtotal for i in items)`.
- **A y B**: cada ítem gravado lleva su alícuota (`Item("Diseño", "1000.00", alicuota=Alicuota.IVA_21)`) y su precio va **sin IVA**. El neto de los ítems por alícuota tiene que coincidir con lo autorizado; para no calcularlo dos veces, autorizá con `neto=neto_por_alicuota(items)`. Los ítems sin alícuota cuentan como no gravados o exentos.
- **A**: la tabla agrega las columnas "% IVA" y "Subtotal c/IVA", y los totales discriminan el neto gravado, el IVA de cada alícuota, el exento y el no gravado.
- **B**: los precios se muestran con IVA incluido y, si el receptor es consumidor final, se imprime el "Régimen de Transparencia Fiscal al Consumidor" de la Ley 27.743 (IVA contenido y otros impuestos nacionales indirectos). Como el IVA de cada ítem se redondea por separado, la suma de los ítems con IVA puede diferir en centavos del total autorizado, que es el que se imprime en los totales.
- **Notas de crédito/débito**: el título cambia a "NOTA DE CRÉDITO" / "NOTA DE DÉBITO" y se imprime el comprobante o período asociado.
- Si hay tributos, se agrega una tabla "Otros tributos".
- La condición frente al IVA del emisor sale de la letra ("Responsable Monotributo" en C, "IVA Responsable Inscripto" en A y B); se puede fijar con `Emisor(..., condicion_iva="...")`.
- `bonificacion` es un porcentaje de descuento (0 a 100) por ítem. Si algún ítem tiene descuento, la tabla agrega las columnas "% Bonif" e "Imp. Bonif.", y el subtotal del ítem ya lo tiene aplicado.
- Si la condición frente al IVA del receptor es consumidor final, se imprime la leyenda "A CONSUMIDOR FINAL" (RG 1415, Anexo II).
- El documento y la condición frente al IVA del receptor, el período de servicio y el CAE salen del `ComprobanteAutorizado`.
- `copias=("ORIGINAL", "DUPLICADO")` genera una copia por cada etiqueta. Si los ítems no entran en una hoja, se pagina y cada hoja lleva el número de comprobante y "Pág. X/Y".
- `qr_url(factura)` devuelve solo el texto del QR (`https://www.arca.gob.ar/fe/qr/?p=...`), por si querés armar tu propio diseño.
- El PDF usa Liberation Sans, incluida en la librería (licencia SIL OFL 1.1, en `arca_client/wsfe/fonts/OFL.txt`). Tiene las mismas métricas que Helvetica/Arial y cubre, además del español, otros alfabetos latinos, el griego, el cirílico y símbolos como € o ™. Los caracteres que no tiene (emojis, chino, japonés, etc.) se reemplazan por `?`.

Para facturar se necesita autorizar el certificado también al servicio `wsfe` (en homologación, desde WSASS). En producción, además, hace falta un punto de venta habilitado para web services.

## Verificar comprobantes de proveedores (WSCDC)

`WSCDC` consulta a ARCA si un comprobante recibido existe y está autorizado con los datos que figuran en él (`ComprobanteConstatar`). Sirve para controlar las facturas de proveedores antes de registrarlas o pagarlas. No emite nada.

Lo más simple es verificar con el texto del código QR de la factura, que trae todos los datos necesarios:

```python
from arca_client import WSCDC

wscdc = WSCDC(wsaa)  # la CUIT que consulta se toma del certificado
resultado = wscdc.verify_qr("https://www.arca.gob.ar/fe/qr/?p=eyJ2ZXIiOjEs...")
if not resultado.aprobada:
    print("No coincide con ARCA:", resultado.observaciones, resultado.errores)
```

O con los datos impresos, si no hay QR:

```python
from datetime import date
from arca_client import CbteTipo, DocTipo

resultado = wscdc.verify_voucher(
    cuit_emisor=30712345671,
    punto_venta=3,
    tipo=CbteTipo.FACTURA_A,  # o cualquier código de ARCA, ej. 201 (FCE A)
    numero=42,
    fecha=date(2026, 10, 19),
    importe_total="1210.00",
    codigo_autorizacion="76123456789012",  # el CAE
    doc_tipo_receptor=DocTipo.CUIT,  # opcional: para facturas a tu nombre, tu CUIT
    doc_nro_receptor=20123456786,
)
```

- `resultado.aprobada` es `True` solo si ARCA devuelve `Resultado = "A"` sin errores. Si no coincide algún dato, es `False` y `observaciones`/`errores` dicen por qué (ej. `100`: el CAE no existe, `105`: el número no corresponde al CAE, `107`: la fecha no corresponde, `110`: el importe no corresponde).
- **ARCA tolera alrededor de $1 de diferencia en el importe**: en homologación aprobó diferencias de ±1,01 y rechazó ±1,50. Tampoco devuelve el importe registrado. Si necesitás un control exacto, compará además el importe con lo que tenés registrado.
- Si ARCA no llega a procesar la consulta (por ejemplo, falta de autorización), se lanza `WSCDCError`. Eso no dice nada sobre el comprobante: no se pudo verificar.
- `modo=ModoAutorizacion.CAEA` (o `CAI`) para comprobantes autorizados por otra vía. `verify_qr` lo toma del QR.
- `parse_qr_url(texto)` devuelve el JSON del QR (con el importe como `Decimal`), por si querés guardar los datos.
- Consultas: `tipos_comprobante()`, `tipos_documento()`, `modalidades()` y `estado()` (estado de los servidores; no necesita ticket).

Hay que autorizar el certificado al servicio `wscdc` (en homologación, desde WSASS; en producción, desde el *Administrador de Relaciones de Clave Fiscal*).

```bash
ARCA_CERT=cert.crt ARCA_KEY=private.key \
  uv run python examples/verificar_comprobante.py "https://www.arca.gob.ar/fe/qr/?p=..."
```

### Errores

Los SoapFault del WSAA se convierten en `WSAAError`, con `code` (ej. `cms.cert.expired`) y `retryable`. Según la especificación, solo se puede reintentar ante `wsaa.*` y `wsn.unavailable`.

## Limitaciones conocidas

- **Factura de Crédito Electrónica MiPyME (FCE, tipos 201 a 213) no soportada.** Una MiPyME que le vende a una gran empresa por encima del monto mínimo vigente está obligada a emitir FCE, y ARCA rechaza la Factura A, B o C común. Para soportarla faltan:
  - los tipos 201/202/203 (A), 206/207/208 (B) y 211/212/213 (C);
  - el envío de `Opcionales`: CBU (2101) o alias (2102) del emisor y la modalidad de transferencia (27: `SCA` o `ADC`);
  - el vencimiento del pago obligatorio también para productos;
  - en las notas, el comprobante asociado con CUIT y fecha y la anulación (22: `S`/`N`);
  - las leyendas propias en el PDF.

  La consulta de si un receptor está obligado y la aceptación o el rechazo de la FCE son de otro web service (WSFECRED), que tampoco está implementado.
- **Solo pesos.** `MonId` y `MonCotiz` van fijos en `PES` y 1. Para emitir en moneda extranjera faltan la moneda y la cotización como parámetros, `CanMisMonExt` (RG 5616), las consultas `FEParamGetTiposMonedas` y `FEParamGetCotizacion` y el PDF en otra moneda.
- **Otros comprobantes no soportados**: A con leyenda "Operación sujeta a retención" (51 a 54: factura, notas de débito y crédito, y recibo), recibos (4, 9 y 15) y otros tipos de nicho de `FEParamGetTiposCbte` (notas de venta al contado, liquidaciones, cuentas de venta y líquido producto, etc.).
- **PDF sin emojis ni escrituras asiáticas.** Liberation Sans no las cubre: esos caracteres se reemplazan por `?`.

## Desarrollo

```bash
uv run pytest
uv run ruff check .
```

## Licencia

MIT
