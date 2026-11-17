# Arquitectura de arca-client

Cómo se autentica el paquete contra ARCA, cómo pide un CAE y qué hace cuando algo sale mal.
Hay una versión interactiva con más detalle por paso en [`arquitectura.html`](arquitectura.html)
(descargala y abrila en el navegador). Las referencias `archivo:línea` son relativas a
`src/arca_client/` y corresponden al commit `7a49d55`: con el tiempo pueden correrse.

```python
wsaa = WSAA.from_alias("miapp", Environment.HOMOLOGACION)  # lee certs/homologacion/miapp.*
wsfe = WSFE(wsaa)                                         # la CUIT sale del certificado
factura = wsfe.create_invoice_c(punto_venta=1, importe_total="1500.00")  # TA + CAE
factura.numero, factura.cae                               # → QR / PDF
```

Crear los objetos no toca la red. La primera llamada a ARCA ocurre dentro de `create_invoice_c`,
que pide el ticket si hace falta.

## Vocabulario

| Término | Qué es |
|---|---|
| WSAA | Web service de autenticación. Recibe tu firma y devuelve un ticket. |
| TRA | Ticket de Requerimiento de Acceso: el XML que se firma para pedir el ticket. |
| CMS | El TRA firmado con la clave privada (PKCS#7, en Base64). |
| TA | Ticket de Acceso: `token` + `sign`. Vale 12 h y no se puede pedir otro mientras haya uno vigente. |
| WSFEv1 | Web service de factura electrónica. Autoriza comprobantes. |
| CAE | Código de Autorización Electrónico: lo que hace válida a la factura. |
| CbteTipo 11 | Factura C. El número es correlativo por punto de venta y tipo. |
| Homologación | Ambiente de prueba. Emite de verdad (consume numeración), pero sin validez fiscal. |

## Mapa de módulos

```mermaid
flowchart TD
    code["Tu código / examples/*.py"]
    code -->|"create_invoice_c()"| wsfe["WSFE<br/>wsfe/client.py"]
    code -->|"verify_qr()"| wscdc["WSCDC<br/>wscdc/client.py"]
    code -->|"get_constancia()"| wspci["WSPCI<br/>wspci/client.py"]
    code -.->|"uv run"| setup["arca-setup<br/>setup_wizard.py"]

    wsfe -.-> qrpdf["qr.py · pdf.py<br/>(después del CAE)"]
    wsfe -->|"get_ticket('wsfe')"| wsaa
    wscdc -->|"get_ticket('wscdc')"| wsaa
    wspci -->|"get_ticket(...)"| wsaa
    wsaa["WSAA<br/>wsaa/client.py"]

    setup --> certs["certificates.py<br/>clave, CSR, CUIT"]
    certs -->|"escribe 0600"| disk[("certs/&lt;amb&gt;/&lt;alias&gt;.key · .crt")]
    disk -->|"PEM"| wsaa

    wsaa --> tra["tra.py<br/>arma XML"]
    wsaa --> signer["signer.py<br/>firma CMS"]
    wsaa --> ticket["ticket.py<br/>parsea TA"]
    wsaa --> cache["cache.py<br/>guarda TA"]
    cache --> tickets[("~/.cache/arca-client/tickets/*.json")]

    wsaa & wsfe & wscdc & wspci --> transport["transport.py<br/>requests + zeep · timeout · SECLEVEL=1"]

    subgraph ARCA["Servidores de ARCA"]
        s_wsaa["WSAA · LoginCms"]
        s_wsfe["WSFEv1"]
        s_wscdc["WSCDC"]
        s_pci["Padrón A5"]
    end
    transport --> s_wsaa & s_wsfe & s_wscdc & s_pci
```

`SECLEVEL=1` se aplica a todo homologación y, en producción, solo a `servicios1.afip.gov.ar`
(WSFEv1 y WSCDC), que usa una clave DH chica que OpenSSL 3 rechaza.

## Autenticación (WSAA)

Cada operación de WSFE, WSCDC y WSPCI llama a `get_ticket(servicio)`. La clave del caché combina
ambiente, servicio y huella del certificado (`_cache_key`, `wsaa/client.py:128`).

```mermaid
sequenceDiagram
    participant C as WSFE / WSCDC / WSPCI
    participant W as WSAA (wsaa/client.py)
    participant K as FileTicketCache (wsaa/cache.py)
    participant A as WSAA de ARCA

    C->>W: get_ticket("wsfe")  [client.py:74]
    W->>K: get(key)  [cache.py:110]
    alt TA vigente (le quedan más de 5 min)
        K-->>W: AccessTicket
        W-->>C: token + sign, sin tocar la red
    else no hay TA o venció
        K-->>W: None
        W->>K: lock(key): flock sobre #lt;key#gt;.lock  [cache.py:94]
        W->>K: get(key) otra vez: otro proceso pudo haberlo obtenido
        W->>W: build_tra(): XML con ventana de ±10 min  [tra.py:10]
        W->>W: sign_tra(): PKCS#7 SHA-256 → Base64  [signer.py:18]
        W->>A: loginCms(in0=cms)  [client.py:119]
        alt OK
            A-->>W: loginTicketResponse (token, sign, vence en ~12 h)
            W->>K: set(key, ticket): temporal 0600 + os.replace  [cache.py:124]
            W-->>C: AccessTicket
        else Fault coe.alreadyAuthenticated
            A-->>W: ya hay un TA vigente que ARCA no devuelve
            W->>K: get(key): ¿lo guardó otro proceso?  [client.py:88]
            W-->>C: ese TA, o WSAAError: esperar a que venza (hasta 12 h)
        end
    end
```

Por el caso `coe.alreadyAuthenticated`, no hay que borrar `~/.cache/arca-client/tickets/` ni usar
`force=True`. Desde la caché se exige que la carpeta sea privada (0700 y del usuario actual).

## Emisión de una Factura C (WSFEv1)

El número no lo elegís vos: se calcula como el último autorizado + 1 (`_emitir`,
`wsfe/client.py:608`). Por eso el riesgo principal no es un rechazo sino un corte justo después de
enviar, cuando no se sabe si ARCA llegó a autorizar.

```mermaid
sequenceDiagram
    participant U as Tu código
    participant F as WSFE (wsfe/client.py)
    participant W as WSAA + caché
    participant A as WSFEv1 de ARCA

    U->>F: create_invoice_c(1, "1500.00")  [:350]
    F->>F: _create_c / _create: valida todo antes de la red (Decimal, half even, UTC−3)
    F->>W: get_ticket("wsfe")  [_auth :705]
    W-->>F: Token, Sign (+ Cuit del certificado)
    F->>A: FECompUltimoAutorizado(PtoVta, CbteTipo=11)  [:103]
    A-->>F: CbteNro = N
    F->>A: FECAESolicitar(N + 1)  [:625]
    alt Autorizado
        A-->>F: FECAEDetResponse: Resultado "A", CAE, CAEFchVto
        F-->>U: ComprobanteAutorizado  [_parse_cae_response :928]
    else Rechazo 10016: otro proceso usó el número
        A-->>F: Err 10016 (no se emitió nada)
        F->>A: FECompUltimoAutorizado
        A-->>F: N + 1 (si no avanzó, el rechazo es por otra causa → WSFEError)
        F->>A: FECAESolicitar(N + 2), hasta numbering_attempts = 3
        A-->>F: Resultado "A", CAE
        F-->>U: ComprobanteAutorizado
    else Corte, timeout o respuesta no reconocible
        A--xF: sin respuesta clara: ARCA pudo haber autorizado
        F->>A: FECompConsultar(N + 1): hasta 3 intentos cada 2 s  [_recover :637]
        alt existe con los mismos datos
            F-->>U: ComprobanteAutorizado con el CAE registrado
        else 602: no existe
            F-->>U: WSFEConnectionError(safe_to_retry=True)
        else otros datos o no se pudo consultar
            F-->>U: WSFEConnectionError(safe_to_retry=False): consultar get_voucher más tarde
        end
    end
```

**Nunca reintentar `create_invoice_c` a ciegas.** Vuelve a calcular el número y, si el primer
intento sí se autorizó, se emiten dos facturas. Los nombres de los campos salen del WSDL
(`tests/fixtures/wsfev1.wsdl`), no del manual.

## Después del CAE: QR, PDF y verificación

```mermaid
flowchart LR
    subgraph Emisor["Emisor (sin red)"]
        fa["ComprobanteAutorizado"] --> qr["qr_url(factura)<br/>wsfe/qr.py:35 · RG 4892"]
        qr --> pdf["render_invoice_pdf()<br/>wsfe/pdf.py:131 · extra [pdf]"]
        pdf --> out[("PDF con QR")]
    end
    subgraph Receptor["Receptor (consulta a ARCA)"]
        v["WSCDC.verify_qr(url)<br/>parse_qr_url valida antes"] --> cc["ComprobanteConstatar"]
        cc --> res["Constatacion<br/>A = aprobado · R = no"]
    end
    out -.->|"lee el QR"| v
```

El QR codifica `https://www.arca.gob.ar/fe/qr/?p=` seguido de un JSON en Base64 con CUIT, punto
de venta, tipo, número, importe y CAE. El PDF valida que los ítems sumen lo que ARCA autorizó.

## Certificado y clave (`arca-setup`)

Se hace una vez por ambiente. La clave privada se genera localmente y nunca sale de la máquina: a
ARCA solo viaja el CSR, que es público.

```mermaid
sequenceDiagram
    participant S as arca-setup
    participant D as Disco (certs/#lt;amb#gt;/)
    participant Web as Vos, en ARCA web
    participant A as WSAA

    S->>S: collect_data(): ambiente, CUIT (módulo 11), razón social, alias  [setup_wizard.py:124]
    S->>D: #lt;alias#gt;.key: RSA 2048, 0600  [setup_wizard.py:162]
    S->>D: #lt;alias#gt;.csr: C=AR, O=razón social, CN=alias, serialNumber=CUIT  [certificates.py:57]
    alt Homologación
        S->>Web: pegás el CSR en WSASS › Nuevo Certificado  [setup_wizard.py:216]
        Web-->>S: pegás el .crt que devuelve WSASS
    else Producción
        S->>Web: subís el .csr en Administración de Certificados Digitales  [setup_wizard.py:254]
        Web-->>S: descargás el .crt (Ver › Descargar)
    end
    S->>S: certificate_matches_key(): ¿el cert es de esta clave?  [setup_wizard.py:305]
    S->>D: #lt;alias#gt;.crt
    Web->>Web: autorizar el certificado al servicio (wsfe, wscdc...)
    S->>A: try_login: get_ticket(servicio)  [setup_wizard.py:324]
    A-->>S: primer TA, queda en caché 12 h
```

Después, `WSAA.from_alias("miapp", env)` encuentra `certs/#lt;amb#gt;/miapp.crt` y `.key`
(`certificate_paths`, `certificates.py:95`). La CUIT que usa WSFE sale del `serialNumber` del
certificado (`cuit_from_certificate`, `certificates.py:109`).

## Errores

Todos heredan de `ArcaError` (`exceptions.py`). La pregunta importante es siempre: ¿se emitió algo?

| Excepción | Cuándo | ¿Se emitió? | Qué hacer |
|---|---|---|---|
| `WSAAError` | El WSAA rechazó el login (`.code`, ej. `coe.alreadyAuthenticated`, `cms.cert.expired`). | Nada | Si `.retryable` (`wsaa.*`, `wsn.unavailable`), reintentar después de 60 s. Si es `alreadyAuthenticated` sin TA en caché, esperar a que venza. |
| `WSFEError` | Rechazo explícito de ARCA (`.codes`, errores, observaciones). | No | Corregir los datos. Un rechazo no consume número. |
| `WSFEConnectionError` | Corte al pedir el CAE. | Ver `safe_to_retry` | `True`: se puede reintentar. `False`: no reintentar, consultar `get_voucher(pv, tipo, err.numero)`. |
| `RespuestaNoReconocida` | Respuesta que no se pudo interpretar. Dentro de `create_*` dispara la verificación. | Quizás | Nunca tratarla como rechazo. La respuesta completa está en `.respuesta`. |
| `WSCDCError` | Error de la constatación. | — | Solo consulta: es seguro reintentar. |
| `WSPCIError` · `PersonaNoEncontrada` | Error del padrón o CUIT inexistente. | — | Solo consulta. Una CUIT inactiva no lanza error: viene en `errores_constancia`. |
