# arca-client

Cliente Python para los web services SOAP de ARCA (ex AFIP): WSAA (autenticación) y WSFEv1 (Factura C).
Código, docstrings, mensajes y README en español.

## Comandos
- `uv run pytest` — tests (no tocan la red)
- `uv run ruff check . && uv run ruff format .`
- `uv run arca-setup` — asistente interactivo (no se puede correr desde Claude: necesita teclado)

## Reglas importantes
- **No asumir nombres de campos por el manual de WSFEv1**: sus ejemplos XML tienen errores
  (dice `FEDetResponse`, el real es `FECAEDetResponse`; `Observaciones.Obs`). La fuente de verdad
  es el WSDL: `tests/fixtures/wsfev1.wsdl`. Los fakes de los tests se arman con esos tipos (`_t(...)`).
- **Nunca reintentar `create_invoice_c` a ciegas**: recalcula el número y puede duplicar la factura.
  Ante cortes se verifica con `get_voucher` (FECompConsultar). Una respuesta que no se entiende
  jamás se informa como rechazo.
- **Llamadas reales a ARCA**: en homologación, `FECAESolicitar` emite comprobantes de verdad
  (sin validez fiscal, pero consumen numeración). Avisar antes de emitir; consultar
  (`last_voucher_number`, `get_voucher`) es seguro.
- **Ticket de acceso (TA)**: dura 12 h y ARCA no permite pedir otro mientras haya uno vigente
  (`coe.alreadyAuthenticated`). No borrar `~/.cache/arca-client/tickets/` ni forzar `force=True`.
- **Datos personales**: el repo es público. Nunca commitear `certs/`, PDFs generados, CUIT ni CAE
  reales (en tests usar valores ficticios, ej. CUIT 20123456786). Revisar el diff antes de cada commit.
- Importes siempre `Decimal`/`str`/`int`, nunca `float`; redondeo half even (criterio de ARCA).
- Homologación necesita `SECLEVEL=1` en TLS (`transport.py`); todas las llamadas usan `operation_timeout`.

## Commits
Mensajes en español. Solo commitear/pushear cuando el usuario lo pide.
