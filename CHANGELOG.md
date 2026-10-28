# Cambios

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/); versiones según [SemVer](https://semver.org/lang/es/) (en `0.x`, la versión minor puede traer cambios de comportamiento: están marcados abajo).

## [0.4.0] - 2026-10-27

### Agregado

- Notas de crédito y débito B (`create_credit_note_b` / `create_debit_note_b`, tipos 8 y 7) y C (`create_credit_note_c` / `create_debit_note_c`, tipos 13 y 12). Llevan los mismos argumentos que la Factura B o C, más `asociado=` o `periodo=`. El PDF las muestra como las notas A.
- `CbteTipo` suma `NOTA_DEBITO_B`, `NOTA_CREDITO_B`, `NOTA_DEBITO_C` y `NOTA_CREDITO_C`.
- **Constatación de comprobantes (WSCDC)**: `WSCDC.verify_voucher(...)` y `WSCDC.verify_qr(texto_del_qr)` verifican en ARCA las facturas recibidas de proveedores y devuelven una `Constatacion` (`aprobada`, `observaciones`, `errores`). También: `tipos_comprobante()`, `tipos_documento()`, `modalidades()` y `estado()`. Nuevos `WSCDCError` y `ModoAutorizacion`.
- `parse_qr_url(texto)`: lee el JSON del QR de un comprobante (RG 4892).
- Ejemplo `examples/verificar_comprobante.py`.
- Los ejemplos admiten claves privadas con contraseña: `ARCA_KEY_PASSWORD` o, si no está definida, la piden por teclado.

### Cambiado

- **PDF**: usa Liberation Sans (incluida, licencia SIL OFL 1.1) en lugar de Helvetica. El diseño no cambia (mismas métricas), pero ahora se imprimen tal cual los guiones largos, las comillas tipográficas, €, ™, el griego, el cirílico, etc. Solo los caracteres que la fuente no tiene (emojis, CJK) se reemplazan por `?`. El PDF pesa unos 30 KB más porque incluye un subconjunto de la fuente.
- El extra `pdf` declara `fonttools` (ya venía como dependencia de `fpdf2`).

### Corregido

- **Producción**: WSFEv1 (y WSCDC) no conectaban con OpenSSL 3 (`DH_KEY_TOO_SMALL`), porque `servicios1.afip.gov.ar` usa la misma clave DH chica que homologación. Ahora se acepta `SECLEVEL=1` solo para ese servidor, sin dejar de verificar el certificado ni el host. El WSAA de producción sigue con la configuración TLS por defecto.

## [0.3.0] - 2026-10-26

### Agregado

- `cuit_from_certificate` es parte de la API pública (`from arca_client import cuit_from_certificate`) y acepta, además de un `x509.Certificate`, el PEM en bytes o una ruta, igual que `WSAA(cert=...)`.
- `certificate_paths(alias, env, base_dir="certs")`: rutas `<base_dir>/<ambiente>/<alias>.crt|.key` donde guarda los archivos `arca-setup`.
- `WSAA.from_alias(alias, env, base_dir="certs", **kwargs)`: carga el certificado y la clave desde esa convención; si falta alguno, `ArcaError` con la ruta buscada.

### Cambiado

- `arca-setup` pide la carpeta base (por defecto `certs`) en lugar de la carpeta final, y siempre guarda en `<carpeta base>/<ambiente>/`. Con los valores por defecto, las rutas son las mismas que antes.

## [0.2.0] - 2026-10-26

### Agregado

- **Responsable inscripto**: `create_invoice_a`, `create_invoice_b`, `create_credit_note_a` y `create_debit_note_a`. Los importes se pasan como neto por alícuota (`neto={Alicuota.IVA_21: "1000.00"}`) y la librería calcula el IVA (half even) y el total. Admiten `exento=`, `no_gravado=` y `tributos=`.
- Otros tributos (`Tributo`, `TipoTributo`): percepciones, impuestos internos, etc., también en `create_invoice_c`.
- Nuevos tipos: `Alicuota`, `IvaAlicuota`, `ComprobanteAsociado`; `CbteTipo` suma `FACTURA_A`, `NOTA_DEBITO_A`, `NOTA_CREDITO_A` y `FACTURA_B`.
- `ComprobanteAutorizado` suma (con valores por defecto) `importe_neto`, `importe_iva`, `importe_no_gravado`, `importe_exento`, `importe_tributos`, `iva`, `tributos`, `asociados` y `periodo_asociado`. `get_voucher` los completa.
- Consultas de parámetros: `condiciones_iva_receptor(clase)`, `tipos_iva()` y `tipos_tributo()`.
- PDF de comprobantes A, B y notas de crédito/débito: IVA discriminado en la A, precios con IVA y leyenda de la Ley 27.743 en la B, comprobante asociado y tabla de otros tributos. `Item.alicuota` y `neto_por_alicuota(items)`.
- Ejemplo `examples/factura_a.py`.
- `FileTicketCache` serializa los pedidos de TA de varios procesos con un archivo de bloqueo; los backends propios pueden hacer lo mismo con un método `lock(key)` opcional.

### Cambiado

- **Error 10016 al emitir**: si otro proceso usó el número, se reintenta con el siguiente (hasta `numbering_attempts`, 3 por defecto) en vez de lanzar `WSFEError`. Si el último autorizado no avanzó, el 10016 se sigue informando como antes.
- **CUIT/CUIL del receptor inválida**: ahora se rechaza localmente con `ValueError` (dígito verificador), antes de llamar a ARCA. Antes llegaba como `WSFEError`.
- La recuperación ante cortes compara también el neto y el IVA del comprobante encontrado.
- El mensaje de `WSFEConnectionError` sin verificación dice "NO reintentes la emisión" (antes nombraba `create_invoice_c`).
- `Emisor.condicion_iva` es `None` por defecto y se deriva de la letra; en la Factura C sigue imprimiendo "Responsable Monotributo".

### Corregido

- WSAA: se reutiliza el TA aunque el reloj local atrase respecto del de ARCA.
- WSAA: con backends de caché sin `lock(key)`, ante `coe.alreadyAuthenticated` por pedidos simultáneos se relee el caché una vez antes de fallar.

## [0.1.0]

Versión inicial: WSAA (TRA, firma CMS, caché de tickets), asistente `arca-setup`, Factura C con recuperación ante cortes, código QR y PDF.
