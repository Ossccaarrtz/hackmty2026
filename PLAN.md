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
- Auditoría manual en vivo (correr los 4 checkpoints + 8 casos de chat adversariales contra el API real): `score`/`alerts` en los checkpoints Día 63 y Día 90 venían calculados **antes** de ejecutar la acción de ese mismo paso — el frontend podía ver "leak activo" junto con el texto "ya lo resolví" en la misma respuesta. Corregido: `lambda_advance_day.py` recalcula `signals` sobre el estado ya escrito antes de responder, para esos dos checkpoints.

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

## 4. Lo que falta del lado de backend/agente (si alguien tiene tiempo para seguirle)

- ~~Capa de lenguaje natural real sobre el agente decisor~~ — **ya resuelto.** `POST /chat/message` usa Gemini (`gemini-3.6-flash`) con tool-calling real sobre `agent_actions.py` — las mismas funciones verificadas de `advance-day`. Probado con casos adversariales (inyección de prompt, alucinación de hechos, bills inexistentes/sanos) — ver tabla de resultados en el README.
- ~~Sincronizar el sweep de ahorro con `/signals` y `/transactions`~~ — **ya resuelto.** `/signals` ahora calcula los totales sumando las transacciones reales (no un registro estático), y el agente registra el sweep como un movimiento real en la tabla. Probado: balance pasa de $506 a $466 automáticamente después de la acción, sin sincronización manual.
- **Guardrail de anomalía** — ~~prometido en el pitch~~ **ya implementado.** `signal_engine.detect_anomaly()` compara el gasto de los últimos 14 días contra el historial propio de la persona; si es más del doble, el agente no ejecuta la acción autónoma sola, genera un `anomaly_pause` pidiendo confirmación en su lugar. Con los datos normales de Mia se mantiene inactivo (no rompe la demo feliz), pero ya no es solo discurso.
- ~~Webhook tras cada transacción~~ — **ya resuelto.** DynamoDB Streams habilitado en `jarbis-financiero-data`, dispara `jarbis-financiero-notifier` automáticamente en cada escritura (sin polling). Probado en vivo: se pidió por chat mover $15 a ahorro → sin llamar nada más, la notificación ya estaba en `GET /notifications` segundos después. Funciona igual sin importar si la acción vino del chat o de `advance-day`.
- ~~Billing de Gemini~~ — **ya resuelto.** Cloud Prepay activado (MXN 100). Probado en vivo: 8 solicitudes seguidas en menos de un minuto, todas exitosas (el límite gratuito era 5/minuto). Modelo de vuelta a `gemini-3.6-flash`.
- **Un segundo escenario/persona** (alguien con ingreso estable) — quedó como idea abierta en la pizarra de equipo, útil para demostrar que el score no castiga a todos igual.
- ~~Apartados de gastos fijos con reparto proporcional~~ — **ya resuelto** (ver sección 4.5). Falta la UI de frontend para crear apartados / declarar el patrón de nómina — hoy solo funciona por chat o por el endpoint REST directo.
- ~~Reporte de confiabilidad exportable~~ — **ya resuelto.** `GET /trust-report` — score + historial real + el historial COMPLETO de acciones verificadas (deduplicado entre `ACTION#` y `NOTIFICATION#`, así no importa si la acción vino del chat, de `advance-day`, o del reparto de apartados) + resumen narrativo determinístico (no generado por LLM). Ver [PITCH.md](./PITCH.md) para el porqué de este endpoint y el framing de negocio (señal para Capital One sobre sus propios clientes de secured card, no un producto que se vende a bancos externos). Probado en vivo end-to-end. Falta: UI de frontend para presentarlo/exportarlo.

## 4.5. Apartados (envelope budgeting con reparto proporcional de nómina) — ✅ construido, desplegado y probado en vivo

Detalle completo con ejemplos de request/response en el [README](./README.md#backend-desplegado-ya-en-la-cuenta-oficial-de-aws-del-equipo), sección "Sexto endpoint en vivo". Resumen de lo que cambió respecto al spec original:
- Todo lo de abajo se implementó tal cual — `agent_actions.verified_allocate_envelopes`, `lambda_envelopes.py` nuevo, `lambda_notifier.py` extendido, 4 tools nuevas en el chat.
- Se agregó `set_income_pattern` como tool de chat (no estaba en el spec original) para poder declarar el patrón de nómina sin usar el endpoint REST directo.
- **Bug encontrado y corregido durante la prueba en vivo:** `signal_engine.py` contaba los repartos a apartados como gasto discrecional (rompía `essential_ratio`) porque no reconocía `category="envelope:*"` como una reasignación interna de dinero. Se agregó `is_neutral()` para tratarlo igual que `savings_transfer`.
- Se resolvió la pregunta abierta del reparto: proporcional entre todos los apartados (no por prioridad), como se proponía.
- Pendiente real: no hay UI en frontend todavía para crear apartados / declarar el patrón de nómina — hoy solo se configura por chat o llamando el endpoint REST directo. `_handle_reset` de `advance-day` tampoco limpia apartados/patrón de nómina (son independientes de la simulación de checkpoints a propósito).

<details>
<summary>Spec original (referencia)</summary>

## 4.5. Spec — Apartados (envelope budgeting con reparto proporcional de nómina)

**Idea:** el usuario define gastos fijos mensuales por categoría (ej. gasolina $2000/mes, comida $3000/mes). Cuando cae un depósito que matchea su patrón de nómina declarado, se reparte proporcional a cada apartado según los días reales transcurridos desde el depósito anterior — sin asumir "semanal" o "quincenal" fijo, porque el ingreso de Mia es irregular (ese es el punto del proyecto, no un detalle a ignorar).

**Problema resuelto explícitamente — nómina vs. depósito random (ej. un amigo te manda $100):** Nessie no da ninguna señal estructurada para distinguirlo (el objeto `deposit` solo trae `amount`, `transaction_date`, `status`, `description` de texto libre que nosotros mismos escribimos al sembrar). Pattern-matching sobre texto es frágil. Solución: el usuario declara su patrón de ingreso esperado **una sola vez** (monto aproximado + frecuencia), y cada depósito nuevo se compara contra ese patrón declarado con tolerancia. Solo un depósito que matchea dispara el reparto — cualquier otro depósito se suma al balance normal sin tocar los apartados. Misma doctrina anti-alucinación del resto del proyecto: el sistema no adivina, verifica contra algo ya confirmado explícitamente por el usuario.

**Modelo de datos nuevo (mismo table, mismo patrón de `sk`):**
- `sk: "INCOME_PATTERN"` → `{expected_amount, tolerance_pct, frequency_days}` — configurado una vez, por chat o por un campo en el frontend.
- `sk: "ENVELOPE#<categoria>"` → `{category, monthly_target, created_at}` (ej. `ENVELOPE#gasolina` → `monthly_target: 2000`).
- El saldo de cada apartado **no es un campo mutable** — se deriva sumando transacciones reales, igual que ya hacemos con `compute_totals`/`savings_release`. Cada reparto se escribe en Nessie como `savings_transfer` con `category: "envelope:gasolina"`. Nessie no tiene subcuentas, así que el apartado es una vista calculada sobre transacciones etiquetadas, mismo truco que ya usamos para no depender de que Nessie actualice `balance`.

**Disparador — reutiliza infra ya construida, cero polling nuevo:** `lambda_notifier.py` ya reacciona a cada INSERT en DynamoDB Streams. Se le agrega: si el INSERT es `type=="deposit"` y el monto/fecha matchea `INCOME_PATTERN` dentro de tolerancia, llama a `agent_actions.verified_allocate_envelopes(user_id, deposit_amount, deposit_date)`.

**`verified_allocate_envelopes` (nueva función en `agent_actions.py`, mismo módulo que ya usan chat y advance-day):**
1. Carga los apartados del usuario y el `INCOME_PATTERN`.
2. Confirma que el depósito matchea el patrón (si no, no hace nada — es un depósito normal).
3. Calcula días reales transcurridos desde el depósito de nómina anterior.
4. Proporcional por apartado = `monthly_target * dias_transcurridos / 30`.
5. Simula el saldo de la cuenta corriente después de restar la suma de todos los repartos propuestos.
6. Corre `signal_engine.score_liquidity` sobre ese saldo simulado, con el **mismo umbral `LIQUIDITY_WARNING_DAYS = 7`** que ya usa `verified_move_to_savings` — ni un umbral nuevo ni una segunda definición de "seguro".
7. Si el colchón resultante sigue ≥ 7 días → ejecuta autónomo, escribe las transferencias reales en Nessie.
8. Si lo toca o lo cruza (ej. queda con $3 pesos libres) → **no escribe nada**, genera una acción `partial_allocation_pause` con el desglose propuesto (mismo patrón que `anomaly_pause`/`leak_detected`), pendiente de confirmar por chat o por el feed.

**Tools nuevas en el chat (el LLM nunca decide montos, solo dispara la función verificada):**
- `get_envelopes_status` — saldos actuales, meta mensual, próximo reparto estimado.
- `create_envelope` — alta de un apartado nuevo (`category`, `monthly_target`).
- `confirm_pending_allocation` — ejecuta un `partial_allocation_pause` ya propuesto, después de que Mia confirma o ajusta montos en la conversación.

**Endpoints nuevos:**
- `POST /envelopes` — crear/editar apartado.
- `GET /envelopes?user_id=mia` — lista con saldo derivado + estado (`on_track` / `pending_confirmation`).

**Pregunta abierta (decidir antes de programar el paso 8 con varios apartados a la vez):** si hay varios apartados y la nómina no alcanza para todos con el colchón sano, ¿reparto proporcional entre todos (todos reciben menos) o por prioridad (llenar el primero al 100% antes de tocar el siguiente)? Propuesta: proporcional entre todos, más defendible frente a jueces ("nadie se queda en cero arbitrariamente").

</details>

## 5. Notas de seguridad ya resueltas (no hay que volver a decidir esto)

- La política de riesgo real ya está en código, no es una promesa de pitch: mover a ahorro es autónomo, detener un bill SIEMPRE requiere el paso de confirmación antes de ejecutar, y el paso de "ahorro" en Día 90 **verifica que el bill realmente se haya detenido antes** de mover el dinero — si no, no hace nada. Eso es la "verificación anti-alucinación" aplicada en código real, no solo en el discurso del pitch.
- La key de Nessie vive como variable de entorno del Lambda (`NESSIE_API_KEY`), no hardcodeada en código nuevo.

## 6. Pendientes (para retomar el trabajo sin tener que releer todo el documento)

**Frontend — no consume nada de esto todavía:**
- `GET/POST /envelopes`, `POST /envelopes/income-pattern`, `POST /envelopes/confirm-allocation` — no hay pantalla para crear apartados, declarar el patrón de nómina, ni ver los saldos. Hoy solo se puede hacer por chat o llamando el endpoint REST directo.
- `GET /trust-report` — no hay pantalla que lo muestre ni forma de exportarlo (PDF/link compartible). Es el endpoint más nuevo y el que más valor de pitch tiene sin UI todavía — buen candidato a priorizar si hay tiempo de frontend.
- El tipo de acción `partial_allocation_pause` (cuando un reparto de apartados queda pendiente de confirmar) no está entre los estados visuales que ya maneja el feed (`action-pending` en `styles.css` cubre `requires_confirmation`/`anomaly_pause`/`leak_detected`, falta agregar este).

**Backend — decisiones/detalles menores, no bloqueantes:**
- `_handle_reset` (usado para reiniciar la demo a Día 0) no limpia los apartados ni el patrón de nómina declarado — es intencional (son independientes de la simulación de checkpoints), pero vale confirmarlo con el equipo antes de una demo en vivo para que no sorprenda a nadie en el escenario.
- La tolerancia default del patrón de nómina es 25% — no se ha validado si es muy laxa o muy estricta más allá de los datos de prueba usados en esta sesión.

**Producto / pitch — ideas abiertas, no comprometidas a construirse:**
- Segundo escenario/persona con ingreso estable — útil para demostrar que el score no castiga a todos igual, pero no hay código ni seed para esto todavía.
- Dominio `.tech` propio para el frontend — CloudFront ya funciona con su URL default (`https://d3ebjiymiktpim.cloudfront.net`), falta solo conectar un dominio si el equipo tiene uno.
- Convertir "consistencia de comportamiento en el tiempo" en un quinto factor real del score (discutido como alternativa más ambiciosa a Apartados para que sí mueva el score) — solo es una idea mencionada, no se ha diseñado.
