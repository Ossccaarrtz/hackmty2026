# Plan de implementación — Centinel One

Este documento existe para que cualquiera del equipo pueda seguir construyendo sin necesitar una explicación en vivo. Todo lo marcado como "en vivo" ya está probado end-to-end contra la cuenta oficial de AWS y el sandbox de Nessie — no es teoría.

## 1. Qué ya existe y funciona ahora mismo

| Endpoint | Qué hace | Estado |
|---|---|---|
| `GET /signals?user_id=mia` | Score (0-100) + desglose de 4 features + alertas + liquidez + proyección | ✅ En vivo |
| `GET /transactions?user_id=mia` | Los 62 movimientos crudos de Mia, con balance corriendo ya calculado | ✅ En vivo |
| `POST /simulation/advance-day?user_id=mia` | Avanza un checkpoint (Día 45 → 62 → 63 → 90), ejecuta la política de riesgo real, escribe de verdad en Nessie | ✅ En vivo, probado punta a punta |
| `POST /simulation/advance-day?user_id=mia&reset=true` | Reinicia la simulación a Día 0 (para ensayar la demo las veces que hagan falta) | ✅ En vivo |

Base URL: `https://qj0vumzrfa.execute-api.us-east-1.amazonaws.com` (está en [`.env.example`](./.env.example)).

**Lo que NO existe todavía:**
- Endpoint de chat conversacional (`POST /chat/message`) — el agente decisor ya toma las decisiones, pero no hay una capa de lenguaje natural encima todavía.
- Login/autenticación real (decidido que no hace falta para la demo).

## 2. Cómo se ve una corrida completa de la demo (ya verificado)

```
POST /simulation/advance-day&reset=true   → "Simulación reiniciada a Día 0"
POST /simulation/advance-day              → Día 45: "tu score de hoy es 67/100"
POST /simulation/advance-day              → Día 62: detecta fuga de Gym Co, PIDE CONFIRMAR (no ejecuta nada)
POST /simulation/advance-day              → Día 63: "confirmaste" → detiene el cargo DE VERDAD en Nessie
POST /simulation/advance-day              → Día 90: mueve $40 a ahorro DE VERDAD en Nessie
GET  /signals                             → score subió de 64 a 74, alertas en cero
```

Cada uno de esos 4 pasos regresa un objeto `new_actions` con el texto exacto que debería aparecer en el chat/feed — el frontend puede tomar ese texto literal para poblar la Pantalla 1 (feed) y la Pantalla 2 (chat) sin necesitar el endpoint de chat todavía.

## 3. Plan por fases para frontend (en orden, sin bloquearse)

**Fase 0 — Setup (15 min)**
- Copiar [`.env.example`](./.env.example) a `.env`.
- Probar los 3 endpoints con `curl` o Postman antes de escribir una línea de UI, para ver el shape real.

**Fase 1 — Layout estático (no bloqueante, se puede hacer sin internet)**
- Armar el layout de la Pantalla 1 (Dashboard) con el JSON mock de la sección "Frontend — qué construir" del README.
- Score hero, 4 barras, tarjeta de alerta, colchón de liquidez, proyección.

**Fase 2 — Conectar a `/signals` real**
- Reemplazar el mock por `fetch(`${API_BASE}/signals?user_id=mia`)`.
- Manejar estados: loading, error, alerts vacío (cuando ya no hay fugas).

**Fase 3 — Vista de detalle con `/transactions`**
- El "ver detalle" colapsado de transacciones — usar `running_balance` para una gráfica de línea sin recalcular nada.
- `summary.by_category` ya viene listo para un desglose simple si hace falta.

**Fase 4 — El feed de acciones y el chat, usando `/simulation/advance-day`**
- Poner un botón "Avanzar día" que llama este endpoint y agrega `new_actions[].text` al feed/chat.
- Esto ya da vida a la Pantalla 2 (Chat) sin esperar a un endpoint de chat conversacional — los mensajes son reales, solo que pre-escritos por el agente en vez de generados por un LLM en el momento. Es una simplificación honesta, no un mock.
- Antes de la demo real: llamar `&reset=true` para dejar todo en Día 0.

**Fase 5 — Deploy iterativo**
```
aws s3 sync build/ s3://centinel-one-frontend --region us-east-1
```
URL en vivo: `http://centinel-one-frontend.s3-website-us-east-1.amazonaws.com`. CloudFront + dominio `.tech` se conectan hasta el final, no antes (ver razones en README, sección Arquitectura).

## 4. Lo que falta del lado de backend/agente (si alguien tiene tiempo para seguirle)

- ~~Capa de lenguaje natural real sobre el agente decisor~~ — **ya resuelto.** `POST /chat/message` usa Gemini (`gemini-3.1-flash-lite`) con tool-calling real sobre `agent_actions.py` — las mismas funciones verificadas de `advance-day`. Probado con casos adversariales (inyección de prompt, alucinación de hechos, bills inexistentes/sanos) — ver tabla de resultados en el README. **Pendiente antes de presentar:** confirmar que la cuota gratuita de la key alcance para la demo en vivo, o habilitar billing — el free tier de `gemini-3.6-flash` (el modelo que Google recomienda) es de solo 20 solicitudes/día, por eso terminamos en el modelo lite.
- ~~Sincronizar el sweep de ahorro con `/signals` y `/transactions`~~ — **ya resuelto.** `/signals` ahora calcula los totales sumando las transacciones reales (no un registro estático), y el agente registra el sweep como un movimiento real en la tabla. Probado: balance pasa de $506 a $466 automáticamente después de la acción, sin sincronización manual.
- **Guardrail de anomalía** — ~~prometido en el pitch~~ **ya implementado.** `signal_engine.detect_anomaly()` compara el gasto de los últimos 14 días contra el historial propio de la persona; si es más del doble, el agente no ejecuta la acción autónoma sola, genera un `anomaly_pause` pidiendo confirmación en su lugar. Con los datos normales de Mia se mantiene inactivo (no rompe la demo feliz), pero ya no es solo discurso.
- ~~Webhook tras cada transacción~~ — **ya resuelto.** DynamoDB Streams habilitado en `jarbis-financiero-data`, dispara `jarbis-financiero-notifier` automáticamente en cada escritura (sin polling). Probado en vivo: se pidió por chat mover $15 a ahorro → sin llamar nada más, la notificación ya estaba en `GET /notifications` segundos después. Funciona igual sin importar si la acción vino del chat o de `advance-day`.
- **Billing de Gemini** — pendiente de que alguien del equipo active facturación en Google Cloud para dejar de depender del free tier (20 solicitudes/día en el modelo recomendado). No bloquea nada mientras tanto — seguimos con `gemini-3.1-flash-lite`.
- **Un segundo escenario/persona** (alguien con ingreso estable) — quedó como idea abierta en la pizarra de equipo, útil para demostrar que el score no castiga a todos igual.

## 5. Notas de seguridad ya resueltas (no hay que volver a decidir esto)

- La política de riesgo real ya está en código, no es una promesa de pitch: mover a ahorro es autónomo, detener un bill SIEMPRE requiere el paso de confirmación antes de ejecutar, y el paso de "ahorro" en Día 90 **verifica que el bill realmente se haya detenido antes** de mover el dinero — si no, no hace nada. Eso es la "verificación anti-alucinación" aplicada en código real, no solo en el discurso del pitch.
- La key de Nessie vive como variable de entorno del Lambda (`NESSIE_API_KEY`), no hardcodeada en código nuevo.
