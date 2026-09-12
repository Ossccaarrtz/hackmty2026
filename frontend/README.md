# Centinel One — frontend conectado

React + Vite. Conserva las tarjetas, fondo y distribución del diseño FundFlow y utiliza los endpoints documentados en `PLAN.md`.

```powershell
cd frontend
Copy-Item .env.example .env
npm install
npm run dev
```

Vite carga `.env` desde `frontend/`. Solo se necesitan `VITE_API_BASE_URL` y `VITE_USER_ID` (por defecto, endpoint AWS del equipo y `mia`). No copies `NESSIE_API_KEY` al frontend: todas las variables `VITE_*` son públicas. Reinicia Vite al cambiar configuración.

## Conexiones

- `GET /signals?user_id=mia`: score, cuatro componentes, tendencia real, anomalía, cobertura y alertas.
- `GET /transactions?user_id=mia`: todos los movimientos, ingresos y saldo `running_balance` calculado por el backend. Se muestran seis recientes y el detalle completo permite buscar y ver la gráfica del ledger.
- `POST /simulation/advance-day?user_id=mia`: pide autorización explícita antes de enviar, incorpora los textos literales de `new_actions` y registra el score del checkpoint. Después actualiza signals y transactions.
- `POST /simulation/advance-day?user_id=mia&reset=true`: pide confirmar el reinicio antes de enviarlo.
- `POST /chat/message`: chat real (Gemini + tool-calling). Las acciones que ejecuta se unen al mismo feed que `advance-day`.
- `GET /notifications?user_id=mia`: avisos generados por un webhook real (DynamoDB Streams) — panel en el home + modal completo.

La pantalla inicial no usa saldos, transferencias, tipos de cambio ni gráficos ficticios. Carga y errores son visibles; si una actualización falla, los últimos datos se identifican como anteriores. Las mutaciones no tienen reintentos automáticos y se bloquean mientras hay una solicitud pendiente.

## Límites del backend actual

- Ya existe chat libre real (`POST /chat/message`, Gemini + tool-calling) — la segunda pantalla es una conversación de verdad, no un replay.
- El historial de acciones y checkpoints se guarda en `sessionStorage` por endpoint/usuario. Solo representa respuestas recibidas en esa pestaña; no existe lectura del log ni del checkpoint actual (aunque `/notifications` sí expone en tiempo real los movimientos reales que cualquier pestaña ejecute). Otra pestaña o integrante puede cambiar el sandbox compartido.
- Por esa falta de consulta y porque el backend asume confirmación en Día 63, cada avance explica los posibles efectos y solicita autorización. Esto es una protección de UI, no una validación de autorización del servidor. Producción requiere un contrato de confirmación y estado en backend.
- El score hero viene de `/signals` (historial completo); el gráfico de progreso registra scores de checkpoints, calculados para sus fechas y antes de ejecutar la acción. No se mezclan como una sola serie.
- Los movimientos de ahorro (mover y liberar) **ya se sincronizan** al ledger de `/transactions` y a `/signals` automáticamente — el backend calcula los totales sumando transacciones reales, no un registro estático.
- Reiniciar no revierte movimientos de ahorro **hechos vía chat en Nessie** (sí limpia el estado en DynamoDB y reactiva el bill de Gym Co). El backend puede devolver éxito aunque no haya logrado reactivar el bill: revisar las alertas después del reinicio.
- Ante una respuesta incierta de una mutación se bloquean nuevos avances; revisar el sandbox antes de reiniciar la demo.
- Un 429 del chat (límite de cuota de Gemini) se muestra con el mensaje que el propio backend genera, sin reintento automático.

## Validación y build

```powershell
npm test
npm run build
```

Las pruebas usan un transporte simulado para verificar contratos, signos, datos vacíos, mensajes, duplicados, errores y timeouts sin escribir en el sandbox compartido. La salida de producción es `frontend/dist/` (Vite), no `build/`.
