# arca-client

Cliente Python para los web services SOAP de **ARCA** (ex AFIP).

Estado actual: **autenticación (WSAA)** y **factura electrónica (WSFEv1)**: Factura C para monotributistas y Factura A, Factura B y notas de crédito/débito A para responsables inscriptos.

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

Si varios procesos piden el TA a la vez, `FileTicketCache` los serializa con un archivo de bloqueo (`<clave>.lock`): solo uno llama al WSAA y los demás leen el TA del caché. Un backend propio puede hacer lo mismo con un método `lock(key)` que devuelva un context manager (por ejemplo, un lock de Redis); si no lo tiene, ante `coe.alreadyAuthenticated` se relee el caché una vez antes de fallar. El bloqueo de archivo solo coordina procesos de la misma máquina.

## Factura electrónica (WSFEv1)

| Método | Comprobante | Emisor | Receptor |
|---|---|---|---|
| `create_invoice_c` | Factura C (11) | Monotributista | Cualquiera |
| `create_invoice_a` | Factura A (1) | Responsable inscripto | Responsable inscripto o monotributista |
| `create_invoice_b` | Factura B (6) | Responsable inscripto | Consumidor final, exento, no categorizado, etc. |
| `create_credit_note_a` / `create_debit_note_a` | Nota de Crédito A (3) / Nota de Débito A (2) | Responsable inscripto | Como la Factura A |

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

### Notas de crédito y débito A

Llevan los mismos argumentos que `create_invoice_a`, más el comprobante A al que se refieren (`asociado=`) o, si abarcan varios, el período (`periodo=(desde, hasta)`):

```python
nota = wsfe.create_credit_note_a(
    punto_venta=1,
    asociado=factura,  # un ComprobanteAutorizado o ComprobanteAsociado(tipo, punto_venta, numero)
    doc_nro=30712345671,
    condicion_iva_receptor=CondicionIVAReceptor.IVA_RESPONSABLE_INSCRIPTO,
    neto={Alicuota.IVA_21: "100.00"},
)
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

Para facturar se necesita autorizar el certificado también al servicio `wsfe` (en homologación, desde WSASS). En producción, además, hace falta un punto de venta habilitado para web services.

### Errores

Los SoapFault del WSAA se convierten en `WSAAError`, con `code` (ej. `cms.cert.expired`) y `retryable`. Según la especificación, solo se puede reintentar ante `wsaa.*` y `wsn.unavailable`.

## Limitaciones conocidas

- **Comprobantes no soportados**: Factura de Crédito Electrónica MiPyME (tipos 201 en adelante; ARCA rechaza la Factura A a una gran empresa por encima del monto mínimo de FCE), Factura M y A "operación sujeta a retención", notas de crédito/débito B y C, y comprobantes en moneda extranjera.

- **Los ejemplos (`examples/login.py` y `examples/factura_c.py`) no admiten claves privadas con contraseña.** Si la clave se generó con contraseña (por ejemplo, eligiendo esa opción en `arca-setup`), los scripts fallan al cargarla. La librería sí lo soporta con `WSAA(..., key_password=b"...")`; lo que falta es exponerlo en el ejemplo, ya sea con una variable `ARCA_KEY_PASSWORD` o pidiéndola por teclado.
- **PDF solo con caracteres Latin-1.** El PDF usa las fuentes estándar (Helvetica), que cubren el español (á, ñ, ü, °) pero no emojis ni otros alfabetos: esos caracteres se reemplazan por `?`.

## Desarrollo

```bash
uv run pytest
uv run ruff check .
```

## Licencia

MIT
