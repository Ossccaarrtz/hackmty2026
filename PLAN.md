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
- ~~Pronóstico de gastos recurrentes por cadencia (Parte 1)~~ — **ya resuelto.** `signal_engine.forecast_upcoming_expenses` detecta gastos recurrentes por categoría (ej. renta cada ~30 días) a partir de la cadencia real observada — mínimo 3 ocurrencias, descarta categorías con intervalo irregular (coeficiente de variación > 50%). Expuesto en `/signals` (`upcoming_expenses`) y como tool de chat (`get_upcoming_expenses`). Probado en vivo: detectó la renta de Mia a 5 días con 100% de confianza (cadencia perfectamente regular). De regalo, se encontró y corrigió un bug de seguridad real: el guardrail de anomalía estaba apagado fuera de los checkpoints de `advance-day` desde que se construyó (ver README para el detalle).
- **Alertas estacionales (Parte 2 — diciembre, temporada alta, etc.)** — **evaluado con medición real, decisión: no construir por ahora.** El cuello de botella NO es el tiempo de llamadas a Nessie (medido: hasta 5 años de historial cabrían en ~8.5 min de API pura) — es que la versión barata (4-6 meses) no alcanza el propio estándar anti-alucinación del proyecto (`forecast_upcoming_expenses` exige ≥3 ocurrencias reales; un ciclo adicional da un solo diciembre) y que mostrarla en vivo requeriría tocar el guion de demo ya ensayado (`advance-day` no llega a diciembre). Detalle completo con los números medidos en la sección 6 de este documento.
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
- `_handle_reset` (usado para reiniciar la demo a Día 0) no limpia los apartados ni el patrón de nómina declarado — es intencional (son independientes de la simulación de checkpoints), pero vale confirmarlo con el equipo antes de una demo en vivo para que no sorprenda a nadie en el escenario. (Sí limpia `PENDING_STOP_BILL` desde la segunda auditoría, ver sección 7 — ese es un artefacto de conversación de corta vida, no una configuración del usuario, así que no aplica la misma excepción.)
- La tolerancia default del patrón de nómina es 25% — no se ha validado si es muy laxa o muy estricta más allá de los datos de prueba usados en esta sesión.

**Producto / pitch — ideas abiertas, no comprometidas a construirse:**
- Segundo escenario/persona con ingreso estable — útil para demostrar que el score no castiga a todos igual, pero no hay código ni seed para esto todavía.
- Dominio `.tech` propio para el frontend — CloudFront ya funciona con su URL default (`https://d3ebjiymiktpim.cloudfront.net`), falta solo conectar un dominio si el equipo tiene uno.
- Convertir "consistencia de comportamiento en el tiempo" en un quinto factor real del score (discutido como alternativa más ambiciosa a Apartados para que sí mueva el score) — solo es una idea mencionada, no se ha diseñado.

**Alertas estacionales (diciembre, temporada alta) — evaluado con medición real, decisión: NO construir por ahora.**

Se midió en vivo contra el sandbox real de Nessie (10 llamadas cronometradas: 5 `POST /deposits` + 5 `POST /purchases` sobre la cuenta de Mia, con `sleep(150ms)` entre llamadas igual que el seed real, y limpieza posterior) antes de decidir, en vez de asumir el costo:

| Métrica medida | Resultado real |
|---|---|
| Latencia promedio por llamada (excluyendo 1 cold-start de 1.28s) | ~285ms |
| Rango | 267ms – 1280ms |
| Throughput sostenido con el `sleep(150ms)` ya usado en el seed | ~1 transacción cada ~430ms |

**Conclusión #1, y la que invalida la premisa original: el tiempo de llamadas a Nessie NO es el cuello de botella en ninguna de las 3 opciones.** El seed actual (90 días) son 63 transacciones (~25s de tiempo de API puro). Extrapolando linealmente (0.7 txn/día observado):
- **3-5 años completos** (1460-1825 días) ≈ 1,022-1,278 transacciones ≈ **6.8-8.5 minutos** de tiempo de API puro.
- **Un ciclo adicional de 4-6 meses** (150-180 días) ≈ 105-126 transacciones ≈ **42-50 segundos** de tiempo de API puro.

Ambas opciones son baratas en llamadas a Nessie — hasta sembrar 5 años cabría cómodamente en el tiempo restante si el único costo fuera ese. **La decisión de no construir NO se basa en "no alcanza el tiempo de sembrado"** (sería una excusa débil, ya medida y descartada) — se basa en tres costos reales que sí importan:

1. **Rigor estadístico inconsistente con el propio estándar del proyecto.** `forecast_upcoming_expenses` exige ≥3 ocurrencias reales de una categoría antes de reportar una cadencia (regla anti-alucinación ya en código). Sembrar solo 4-6 meses da **un solo diciembre anterior** — alcanza para una comparación factual puntual ("el diciembre pasado gastaste $X en regalos"), pero no para un forecast con score de confianza comparable al que ya existe. Igualar ese rigor (3+ diciembres) exige la opción de 3-5 años completos, no la barata.
2. **La demo actual no llega a diciembre.** El reloj simulado de `advance-day` tiene checkpoints hasta Día 90 (12-sep-2026, coincide con la fecha real de hoy). Para mostrar en vivo "se acerca diciembre" haría falta *además* del histórico hacia atrás, sembrar meses hacia adelante (sept-dic 2026) y agregar checkpoints nuevos más allá de Día 90 — es decir, tocar el guion de demo ya ensayado (Día 45 → 62 → 63 → 90) a horas de presentar, con el riesgo de romper algo que ya funciona de punta a punta. Esto es un costo de ingeniería/riesgo, no de tiempo de Nessie.
3. **Ajuste con el pitch: una alerta estacional sola es solo informativa.** Cae en la misma trampa ya documentada con "Apartados" en `PITCH.md` — buena evidencia de "acciones verificadas" en abstracto, pero no mueve el Cash-Flow Resilience Score y no es, por sí sola, una *acción* sobre el dinero. Para que valiera la pena tendría que disparar algo accionable y verificado (ej. sugerir/ejecutar un ahorro preventivo antes de diciembre, reusando `verified_move_to_savings` ya existente) — factible en código, pero no cambia los costos #1 y #2 de arriba.

**Por qué no procede ahora, en una frase:** no es "no hay tiempo para sembrar" (medido: sí lo hay, incluso para la opción cara) — es que la versión barata (4-6 meses) no alcanza el propio estándar anti-alucinación del proyecto y requiere tocar el guion de demo ya ensayado, y la versión rigurosa (3-5 años) es proporcionalmente mucho trabajo de diseño/QA para una feature que, sin conectarse a una acción real, es evidencia tangencial de la tesis central — el mismo patrón que ya se identificó con Apartados. El tiempo restante rinde más completando huecos ya identificados y de mayor valor de pitch con menor riesgo (UI de frontend para `/envelopes` y `/trust-report`, ver arriba en esta sección).

**Si el equipo decide retomarlo después del hackatón:** construir la versión conectada a acción (`verified_seasonal_savings` o similar, reusando `verified_move_to_savings`), sembrar 3+ diciembres reales para igualar el rigor de `forecast_upcoming_expenses`, y extender los checkpoints de `advance-day` en un cambio separado y probado con anticipación (no la noche antes de una demo).

## 7. Segunda auditoría (agente independiente, solo lectura) — hallazgos y estado

Se pidió explícitamente buscar el mismo patrón de bug ya encontrado una vez (guardrail que aparenta estar activo pero nunca se ejecuta), más ajuste con el pitch, cobertura de pruebas, e integración frontend-backend. Resultado completo (bueno y malo) — ver también la sección nueva en el README ("Auditoría propia") para el detalle de cada fix:

**Corregidos, ya desplegados y probados en vivo:**
- `verified_allocate_envelopes` calculaba `anomaly.detected` pero nunca lo leía, y no tenía tope de monto — la única acción que se dispara 100% sola. Ahora respeta ambos, igual que `move_to_savings`/`release_savings_buffer`.
- `stop_subscription` en el chat ejecutaba en una sola llamada, dependiendo de una instrucción de prompt para no cancelar prematuramente — exactamente lo que el proyecto dice explícitamente que nunca debe pasar. Ahora es de dos pasos (`stop_subscription` propone, `confirm_stop_bill` ejecuta), como ya funcionaba para apartados vía `PENDING_ALLOCATION_SK`.
- `evaluate_bills` comprobaba "sin actividad jamás" en vez de "sin actividad reciente" (sin ventana de tiempo, a pesar de que el texto de la alerta sí menciona una). Corregido con una ventana de 60 días (`LEAK_LOOKBACK_DAYS`).
- Las tools de solo consulta del chat (`get_envelopes_status`, `get_upcoming_expenses`) no traían `ok`, así que el frontend las pintaba como "acción rechazada" en el feed de acciones — justo la sección que el README señala como la prueba visual de que el agente actúa. Corregido en backend (`ok: true`) y frontend (exclusión explícita por nombre en `data.js`, igual que ya existía para `get_status`).
- Los repartos a apartados se colaban al desglose de gasto por categoría en `/transactions` y en `CategorySpending.jsx` — el fix de `is_neutral()` solo se había aplicado al cálculo del score, no a este endpoint. Corregido en ambos lados.
- `_handle_reset` ahora también limpia `PENDING_STOP_BILL` (artefacto de conversación de corta vida, a diferencia de apartados/patrón de nómina que sí son configuración intencional del usuario).

**Señalado pero NO corregido — decisión consciente de no tocarlo ahora:**
- `forecast_upcoming_expenses`/`get_upcoming_expenses` es evidencia débil (o nula) de la frase central del pitch — es puramente informativo, no ejecuta ninguna acción ni mueve el score. El propio README usa esa misma definición ("solo informa, no ejecuta nada") para diferenciarse de Monarch/Copilot en la tabla comparativa — la auditoría señala que, dentro de Centinel One, esta feature específica cae en la misma categoría. No se revirtió (sigue siendo información honesta y barata, sin fabricar nada), pero **no debe presentarse como evidencia de "toma acciones reales"** en el pitch — ver autocrítica agregada en `PITCH.md`.
- Riesgo de colisión de historial de score entre "hoy real" y "Día 90 simulado" cuando ambas fechas coinciden (pasa hoy por diseño de la demo, pero a partir de mañana un `/signals` sin `as_of` empezará a usar una fecha posterior a cualquier checkpoint sembrado) — riesgo operativo de corta vida útil si la demo se re-ensaya en otra fecha, no de seguridad. No se corrigió porque no afecta la ventana real de la demo.

**Fortalezas confirmadas por la auditoría (vale la pena decirlas en el pitch, no solo internamente):**
- La escritura condicional atómica de `claim_checkpoint` (`ConditionExpression` de DynamoDB) es una solución de concurrencia genuinamente correcta para un entorno serverless sin estado compartido — no un mutex de juguete.
- El patrón de re-verificación en `agent_actions.py` (4-5 chequeos independientes recalculados contra el estado real en cada llamada) es sólido donde se aplica completo — la falla encontrada fue de aplicación incompleta, no de diseño.
- La deduplicación por cercanía de timestamp en `get_verified_action_history` (`abs(ts - at) < 5000`) resuelve con elegancia el problema de tener dos fuentes de verdad (`ACTION#`/`NOTIFICATION#`) sin inflar artificialmente el conteo de acciones del reporte de confiabilidad.
- El frontend (`data.js`) lanza excepciones explícitas ante payloads que rompen el contrato esperado en vez de renderizar silenciosamente `undefined` — extiende la ética de "nunca fabricar un dato" del backend a la capa de presentación.
- `api.js` nunca reintenta un `POST` automáticamente (decisión de diseño explícita en comentario) — coherente con que las acciones mueven dinero real; un retry ciego habría reintroducido el problema que `claim_checkpoint` resuelve del otro lado.

**Cobertura de pruebas — ✅ resuelto.** `backend/signals-lambda/test_agent_actions.py`, 33 tests con `unittest`/`unittest.mock` de la stdlib (cero dependencias nuevas, no se empaquetan en ningún Lambda). Mockea `table` (DynamoDB) y las funciones de `nessie_actions` — corre en ~15ms, nunca toca AWS/Nessie real. Cubre `verified_move_to_savings`, `verified_release_buffer`, `verified_allocate_envelopes`, el flujo de dos pasos de `propose_stop_bill`/`confirm_stop_bill`/`verified_stop_bill`, la deduplicación de `get_verified_action_history`, y `deposit_matches_income_pattern`.

**Verificado que el suite de verdad atrapa regresiones, no solo que pasa en verde:** se reintrodujeron temporalmente (y se revirtieron de inmediato) los dos bugs de seguridad ya corregidos esta sesión — el guardrail de anomalía ignorado en `verified_allocate_envelopes` y la ejecución prematura en `propose_stop_bill` — y en ambos casos el test correspondiente falló como se esperaba antes de restaurar el fix real. Correr con `python -m unittest test_agent_actions -v` desde `backend/signals-lambda/`.
