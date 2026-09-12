# Plan de implementación — Centinel One

Este documento existe para que cualquiera del equipo pueda seguir construyendo sin necesitar una explicación en vivo. Todo lo marcado como "en vivo" ya está probado end-to-end contra la cuenta oficial de AWS y el sandbox de Nessie — no es teoría.

## 1. Qué ya existe y funciona ahora mismo

| Endpoint | Qué hace | Estado |
|---|---|---|
| `GET /signals?user_id=mia` | Score (0-100) + desglose de 4 features + alertas + liquidez + proyección | ✅ En vivo |
| `GET /transactions?user_id=mia` | Los 62 movimientos crudos de Mia, con balance corriendo ya calculado | ✅ En vivo |
| `POST /simulation/advance-day?user_id=mia` | Avanza un checkpoint (Día 45 → 62 → 63 → 90), ejecuta la política de riesgo real, escribe de verdad en Nessie | ✅ En vivo, probado punta a punta |
| `POST /simulation/advance-day?user_id=mia&reset=true` | Reinicia la simulación a Día 0 (para ensayar la demo las veces que hagan falta) | ✅ En vivo |
| `POST /chat/message` | Chat real con tool-calling (Gemini `gemini-3.6-flash`) sobre `agent_actions.py`: `get_status`, `stop_subscription`, `move_to_savings`, `release_savings_buffer` | ✅ En vivo |
| `GET /notifications?user_id=mia` | Avisos disparados por DynamoDB Streams cada vez que el agente hace un movimiento real, sin polling | ✅ En vivo |

Base URL: `https://qj0vumzrfa.execute-api.us-east-1.amazonaws.com` (está en [`.env.example`](./.env.example)).

**Login/autenticación:** ya existe en el frontend (pantalla `Login`, credenciales de demo precargadas). No valida contra un backend de auth real — es solo la puerta de entrada de la demo, con `remember me` guardado en `localStorage`. Decidido que eso es suficiente para el hackathon.

**Lo que NO existe todavía:**
- `get_score_history` está implementado en `agent_actions.py` y ya declarado como tool en el `lambda_chat.py` de este repo (permite que el chat responda "¿cómo estaba mi score en septiembre?" con datos reales, no inventados), pero **no está desplegado en el Lambda `jarbis-financiero-chat` en vivo todavía** — se dejó solo comiteado para no pisar cambios en paralelo de un compañero sobre el mismo archivo. Falta coordinar el deploy.

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
- Probar los endpoints con `curl` o Postman antes de escribir una línea de UI, para ver el shape real.

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
npm run build
aws s3 sync dist/ s3://centinel-one-frontend --region us-east-1 --delete
```
**Ojo:** el output de `vite build` es `dist/`, no `build/` — no hay `vite.config.js` en el repo que redirija el `outDir` (se debe haber perdido en algún merge). Si copias el comando viejo con `build/` no vas a subir nada nuevo y el sync fallará silenciosamente o subirá una carpeta vacía/vieja.

**CloudFront ya conectado: `https://d3ebjiymiktpim.cloudfront.net`** (HTTPS real). El bucket de S3 directo sigue vivo para pruebas rápidas: `http://centinel-one-frontend.s3-website-us-east-1.amazonaws.com`. Falta solo el dominio `.tech` propio si el equipo lo tiene.

## 3.5. Auditoría de código (2 agentes, uno por backend/frontend) — todo lo crítico e importante ya resuelto

Se lanzaron dos revisiones independientes buscando bugs reales (no solo estilo). Resultado completo:

**Backend — corregido:**
- `as_of` inválido o muy anterior a los datos ya no tumba `/signals` con 500 (validación + manejo de listas vacías)
- Condición de carrera en el checkpoint de Día 90 (doble ejecución del sweep con dos clicks/reintentos simultáneos) — corregida con escritura condicional atómica en DynamoDB, probada matemáticamente: el segundo reclamo del mismo checkpoint es rechazado
- `GEMINI_MODEL` ya no cae a un modelo roto (`gemini-2.5-flash`) si la variable de entorno se pierde en un redeploy
- `move_to_savings` ahora valida tope diario acumulado y que no deje el colchón de liquidez por debajo de 7 días — antes solo validaba el monto de una sola transacción
- Colchón de liquidez ya no divide entre 90 fijo — usa días reales transcurridos (antes inflaba el colchón a la mitad de su valor real en checkpoints parciales)
- `trend` y `projection` ahora vienen de un historial real de scores persistido (`SCORE#` en DynamoDB) — antes eran literales inventados
- Fechas hardcodeadas reemplazadas por fecha real; try/except agregado alrededor de las escrituras a Nessie en `agent_actions.py`
- Nueva alerta `liquidity_warning` cuando el colchón cubre menos de 7 días

**Backend — nueva funcionalidad construida (no solo reportada):**
- `verified_release_buffer` — el suavizado de ingreso irregular que el README ya prometía como resuelto pero no existía en código. Ya está implementado, expuesto como tool de chat (`release_savings_buffer`), y probado (rechaza sin fondos, rechaza sobre lo disponible, ejecuta un monto legítimo).

**Frontend — corregido:**
- El chat ya no es un replay del feed de `advance-day` — es un chat real con input libre contra `POST /chat/message`
- `/notifications` ya se consume (antes: cero referencias en todo el código)
- `score.trend`, `signals.anomaly` y `projection.weeks_to_ready` ya se muestran (antes: calculados por el backend pero invisibles)
- Estados visuales distintos para "esperando confirmación" vs "ejecutado" vs "error real" en el feed de acciones
- Manejo explícito de 429 (cuota de Gemini) con el mensaje real del backend
- Texto obsoleto sobre "ahorro no sincronizado" eliminado
- 4 tests nuevos agregados (12/12 pasan), build limpio, desplegado

**Frontend — construido después de la auditoría (no estaba en la lista original):**
- Pantalla de login (credenciales de demo precargadas, `remember me` en `localStorage`)
- Animación de entrada: al iniciar sesión, el dashboard aparece con un "pop" escalonado tarjeta por tarjeta (sidebar → header → score → desglose/progreso → alertas → movimientos), no todo de golpe. Respeta `prefers-reduced-motion`.
- Widget de FAQ flotante (esquina inferior derecha, logo de Capital One) con preguntas frecuentes precargadas
- Filtros de categoría y rango de fechas en el modal de movimientos
- Gráfica de gasto por categoría (`CategorySpending`) usando `summary.by_category`
- Modal de perfil + footer con aviso de privacidad

## 4. Lo que falta (backend, agente y deploy — si alguien tiene tiempo para seguirle)

- ~~Capa de lenguaje natural real sobre el agente decisor~~ — **ya resuelto.** `POST /chat/message` usa Gemini (`gemini-3.6-flash`) con tool-calling real sobre `agent_actions.py` — las mismas funciones verificadas de `advance-day`. Probado con casos adversariales (inyección de prompt, alucinación de hechos, bills inexistentes/sanos) — ver tabla de resultados en el README.
- ~~Sincronizar el sweep de ahorro con `/signals` y `/transactions`~~ — **ya resuelto.** `/signals` ahora calcula los totales sumando las transacciones reales (no un registro estático), y el agente registra el sweep como un movimiento real en la tabla. Probado: balance pasa de $506 a $466 automáticamente después de la acción, sin sincronización manual.
- **Guardrail de anomalía** — ~~prometido en el pitch~~ **ya implementado.** `signal_engine.detect_anomaly()` compara el gasto de los últimos 14 días contra el historial propio de la persona; si es más del doble, el agente no ejecuta la acción autónoma sola, genera un `anomaly_pause` pidiendo confirmación en su lugar. Con los datos normales de Mia se mantiene inactivo (no rompe la demo feliz), pero ya no es solo discurso.
- ~~Webhook tras cada transacción~~ — **ya resuelto.** DynamoDB Streams habilitado en `jarbis-financiero-data`, dispara `jarbis-financiero-notifier` automáticamente en cada escritura (sin polling). Probado en vivo: se pidió por chat mover $15 a ahorro → sin llamar nada más, la notificación ya estaba en `GET /notifications` segundos después. Funciona igual sin importar si la acción vino del chat o de `advance-day`.
- ~~Billing de Gemini~~ — **ya resuelto.** Cloud Prepay activado (MXN 100). Probado en vivo: 8 solicitudes seguidas en menos de un minuto, todas exitosas (el límite gratuito era 5/minuto). Modelo de vuelta a `gemini-3.6-flash`.
- **Un segundo escenario/persona** (alguien con ingreso estable) — quedó como idea abierta en la pizarra de equipo, útil para demostrar que el score no castiga a todos igual.
- **Desplegar `get_score_history` al Lambda `jarbis-financiero-chat` en vivo** — código y tool ya comiteados en este repo (permite preguntas tipo "¿cómo estaba mi score en septiembre?"), pero el Lambda en producción todavía no lo tiene (verificado descargando el código en vivo). Falta coordinar con quien esté tocando ese mismo archivo antes de redesplegar.
- **Confirmar que el deploy del frontend a S3 usa `dist/`, no `build/`** — ver nota en Fase 5. Si el equipo estuvo usando el comando viejo, el sitio en CloudFront puede estar desactualizado desde hace varios commits.

## 5. Notas de seguridad ya resueltas (no hay que volver a decidir esto)

- La política de riesgo real ya está en código, no es una promesa de pitch: mover a ahorro es autónomo, detener un bill SIEMPRE requiere el paso de confirmación antes de ejecutar, y el paso de "ahorro" en Día 90 **verifica que el bill realmente se haya detenido antes** de mover el dinero — si no, no hace nada. Eso es la "verificación anti-alucinación" aplicada en código real, no solo en el discurso del pitch.
- La key de Nessie vive como variable de entorno del Lambda (`NESSIE_API_KEY`), no hardcodeada en código nuevo.
