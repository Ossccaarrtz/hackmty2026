# Centinel One — HackMTY 2026 (Reto Capital One)

Agente financiero que califica a personas sin historial de crédito usando su comportamiento real de flujo de efectivo, y actúa sobre su dinero (no solo aconseja) con una capa de verificación antes de tocar fondos reales.

## El reto

**Pregunta central (Capital One):** cómo aprovechar datos financieros en tiempo real + automatización inteligente para construir herramientas resilientes que empoderen individuos, protejan negocios y salvaguarden capital digital.

**Track elegido: Track 1 — Consumer Financial Autonomy & Credit Building.**

**Rúbrica de evaluación:**

| Criterio | Peso |
|---|---|
| Originality (differentiation, market gap, non-trivial solution) | 30% |
| Technical Depth (data foundation, algorithmic intelligence, system design, demo) | 25% |
| Impact & Feasibility (business model, TAM/SAM/SOM, regulatory, GTM) | 25% |
| Design & Experience (persona, user journey, pitch) | 20% |

**Recurso técnico:** Capital One Nessie Sandbox API (`api.nessieisreal.com`).

## El problema

**Persona: Mia, 24 años**, ingreso por proyecto/freelance, irregular. No tiene ningún archivo en Buró de crédito — no es historial "delgado", es inexistente ("credit invisible"). Ningún banco tradicional puede evaluarla con los modelos clásicos (FICO/VantageScore requieren historial previo que ella no tiene).

**Tamaño de mercado:** ~7M credit invisible + ~25M unscorable en EE.UU. (corrección CFPB, 2025) — más el universo de ingreso variable (gig, freelance, por hora) que no está en esa cifra. TAM ampliado: base completa de clientes de Capital One, ya que el mismo algoritmo funciona para cualquier perfil de ingreso, solo aporta más donde nadie más compite hoy.

**Por qué encaja con Capital One:** ~32% de su cartera de tarjetas es subprime (vs. 14–20% de sus rivales) — está más expuesto a este segmento que la competencia. Su ventaja histórica fundacional fue underwriting basado en datos para encontrar buenos clientes en near-prime/subprime que otros bancos rechazaban por regla fija. Su cliente ideal declarado es el "profitable balance revolver". El score propuesto ayuda a identificar con seguridad a la próxima generación de esos clientes, no es solo una herramienta de bienestar financiero.

**Precedente de industria:** VantageScore 4plus ya usa datos alternativos/cash-flow para calificar a ~33M adultos que FICO no puede. CFPB, Fed, OCC y NCUA emitieron un comunicado conjunto respaldando el uso de datos alternativos en underwriting (con advertencia de fair lending).

## Panorama competitivo

| Producto | Qué hace | Qué no hace |
|---|---|---|
| Zenfi | Categoriza gastos con IA, consulta Buró gratis | Requiere que ya tengas Buró |
| Rocket Money | Detecta y cancela suscripciones | No genera score, no hace forecast |
| Monarch / Copilot | Forecast de cash-flow, insights con IA | Solo informa, no ejecuta nada |
| Cleo | Chat de IA sobre finanzas | Cero acción real |
| **Centinel One** | Score sin Buró + ejecuta acciones reales con verificación | — |

## Arquitectura

```
NESSIE API (datos) → Motor de señales (score + detección) → Agente decisor (política de riesgo + verificación) → interfaz (chat embebido/dashboard) + escritura de vuelta a Nessie
```

**Stack:** React (frontend) + DynamoDB + Lambda + API Gateway + S3/CloudFront, dominio `.tech`. Se reutiliza la arquitectura de un proyecto previo del equipo ("Jarbis": tool-calling, verificación anti-alucinación, backend Lambda + DynamoDB, dashboard React) — la razón para seguir en serverless no es "los datos vienen de una API", es que las acciones del agente son eventos discretos (mensaje de chat, click en "avanzar día", petición del dashboard), no un stream continuo.

**Decisiones de la arquitectura (y por qué):**
- **Interfaz: chat embebido en el propio dashboard, no Telegram.** Un banco real no manda datos financieros por la infraestructura de un tercero. El patrón real es el de **Eno** (el asistente de Capital One): chat dentro de la app, notificaciones dentro de la app. Además elimina una dependencia externa que podría fallar en vivo durante la demo.
- **Lectura del día a día: DynamoDB, no Nessie en vivo.** Confirmamos que Nessie responde lento (~7.6s) y que su `balance` no se actualiza solo. El seed ya vive en Nessie; para el dashboard/score se lee de DynamoDB (espejo rápido y confiable). Nessie solo se toca de verdad cuando el agente **ejecuta** una acción real (`PUT bills`, `POST withdrawals/deposits`) — ahí sí tiene que ser Nessie, es la prueba de que el movimiento es real.
- **"Avanzar día": botón manual, no cron/EventBridge.** Da control total del ritmo durante la demo en vivo en lugar de depender del reloj de pared.
- **CloudFront + dominio `.tech`: solo al final.** Durante desarrollo activo, servir el build de React sin CloudFront (o desde algo con deploy instantáneo) para no perder minutos en cada propagación/invalidación de caché mientras se itera la UI.
- **DynamoDB en una sola tabla**, partition key `customer_id`, sort key con tipo + fecha (`TXN#2026-06-16`, `SCORE#...`, `ACTION#...`) — trae el timeline completo de un cliente con una sola query.
- **Ojo con API Gateway REST (límite de 29s)** si el loop del agente (LLM + verificación + escritura a Nessie + explicación) tarda más — usar HTTP API o confirmar tiempos reales.

**Manejo de datos financieros con el LLM (Claude):** el LLM nunca calcula el score ni decide montos — eso es código determinista. Al LLM solo le llegan señales ya derivadas (ej. "score=62, fuga detectada: Gym Co $40/mes sin uso"), no el historial crudo de transacciones — minimiza qué dato sensible viaja al modelo, por diseño. La verificación anti-alucinación existe para confirmar que lo que el LLM genera coincide con lo que el código ya calculó, antes de tocar dinero real. En términos comerciales, Anthropic no entrena modelos con datos enviados vía API (salvo opt-in explícito) y ofrece retención configurable (hasta cero). Para producción real haría falta un DPA con el proveedor y cumplimiento de GLBA — mismo proceso que cualquier banco ya sigue para usar un proveedor de nube, no es una barrera nueva de la IA.

## Cash-Flow Resilience Score (0–100)

Fórmula propia y explicable — no intenta copiar FICO (fórmula secreta, y de todos modos requiere bureau data que la persona no tiene):

| Feature | Qué mide | Fuente Nessie | Peso |
|---|---|---|---|
| Regularidad de ingreso | Varianza fecha/monto entre depósitos (60–90d) | `deposits` | 35% |
| Ratio esencial/discrecional | % gasto esencial vs. discrecional | `purchases` + `merchants.category` | 25% |
| Recurrencia sana | Bills pagados a tiempo vs. cargos sin uso relacionado | `bills` | 20% |
| Colchón de liquidez | Balance vs. gasto esencial proyectado 7–14d | ledger propio + `purchases` | 20% |

## Política de riesgo del agente

- **Mover dinero a ahorro propio** → autónomo, sin pedir permiso (reversible, sin terceros).
- **Detener un cargo recurrente/suscripción** → siempre pide confirmación, y advierte riesgo contractual explícito ("esto podría tener contrato anual — cancelar antes de tiempo puede mandarte a cobranza").
- Ambas rutas pasan por **verificación anti-alucinación** antes de escribir en Nessie.

Validación real: Capital One ya tiene en su app la función "Block Future Charges", con la misma advertencia sobre que bloquear el cargo no cancela la suscripción con el comercio.

## Funcionalidades

**Innovación clave**
- Cash-Flow Resilience Score explicable, sin Buró
- Política de riesgo diferenciada por tipo de acción
- Verificación anti-alucinación antes de ejecutar acciones financieras reales
- Guardrail de anomalía (compara actividad reciente contra el propio historial antes de actuar)
- Suavizado de ingreso irregular (retiene en semanas buenas, libera monto estable en semanas flacas)

**Soporte**
- Detección de fugas (bills recurrentes sin actividad relacionada)
- Alerta preventiva de insuficiencia de fondos (derivada del colchón de liquidez)
- Proyección de tendencia del score (regresión simple → "listo para producto de crédito en X semanas")
- Ejecución real vía Nessie (`PUT bills`, `POST withdrawals/deposits`)
- Ledger propio (balance calculado — ver hallazgos técnicos)
- Notificaciones proactivas y confirmaciones con contexto vía chat
- Dashboard: score + desglose, timeline de acciones, gráfico de score en el tiempo
- Seed script de historial simulado + control de "avanzar día" para demo

**Descartado (y por qué)**
- Detección de fraude como subsistema aparte — es Track 3, no Track 1, y no da el tiempo.
- "Gastos hormiga" como feature destacada — ya viene gratis del ratio esencial/discrecional, y está sobre-comoditizado en fintech LatAm.
- "Recomendaciones" genéricas — contradice la tesis central (el agente actúa, no solo aconseja).
- Plan de financiamiento/reestructuración de deuda — no aplica a la persona (no tiene ningún producto de crédito que reestructurar, por definición).

## Hallazgos técnicos sobre la Nessie API (confirmados en vivo, no de la documentación)

- `GET /customers`, `/merchants` devuelven `[]` en una key nueva — hay que sembrar todo con POST.
- **`balance` de una cuenta no se actualiza automáticamente** al crear transacciones (probado con transfers, withdrawals, deposits y PUT directo). Solución: tratar Nessie como bitácora de eventos y calcular el balance real sumando movimientos en el propio backend.
- `purchases.amount` parece truncar decimales (39.99 → 39) — usar montos enteros.
- `bills.status` solo acepta `pending | recurring | completed | cancelled`.
- `transfers` está limitado en la versión actual — no acepta `payee_id` ni `medium`. Usar `withdrawals` + `deposits` por separado para simular movimiento entre cuentas.
- Documentación oficial (`/docs`, `/swagger.json`) devuelve 403 — no confiar en ella, solo en pruebas directas.

## Datos sembrados (seed)

~90 días de historial de Mia ya viven en el sandbox de Nessie (ver [`/seed`](./seed)):

| | |
|---|---|
| Depósitos (ingreso freelance irregular) | 8, entre $300 y $720 |
| Compras (renta, súper, transporte, teléfono, discrecional) | 55 |
| Bills recurrentes | Gym Co ($40/mes, `recurring`, sin actividad relacionada — la fuga) + Telco Co ($45/mes, `recurring`, con pago mensual real — control sano) |
| Ingreso total / gasto total | $4,520 / $4,014 |
| Balance real (ledger propio, no el de Nessie) | $506 |

**Motor de señales corriendo contra estos datos ahora mismo:** score = 64/100, 1/2 bills sanos, fuga detectada en Gym Co (~$480/año), colchón cubre 12 días. Prototipo en Node en [`/backend/signal-engine.js`](./backend/signal-engine.js); versión real desplegada en Python en [`/backend/signals-lambda`](./backend/signals-lambda).

## Backend desplegado (ya en la cuenta oficial de AWS del equipo)

Se descubrió que la infraestructura de Jarbis ya vive en esta cuenta (`jarbis-*` tablas/Lambdas + API Gateway `jarbis`) — se reutilizó directamente en vez de crear infraestructura paralela.

| Recurso | Detalle |
|---|---|
| Tabla DynamoDB | `jarbis-financiero-data` — PK `user_id`, SK `sk` (mismo patrón que las tablas `jarbis-*` existentes). También guarda `STATE#simulation` y el log de `ACTION#...` |
| Lambdas | `jarbis-financiero-signals`, `jarbis-financiero-transactions`, `jarbis-financiero-advance-day` — todas Python 3.12, todas usan el rol ya existente `job-search-lambda-role` |
| API Gateway | Rutas `GET /signals`, `GET /transactions`, `POST /simulation/advance-day` agregadas al API `jarbis` ya existente |
| Nessie API key | Variable de entorno `NESSIE_API_KEY` en el Lambda `jarbis-financiero-advance-day` (no hardcodeada) |

El frontend ya puede apuntar a este endpoint real en vez del mock del README — el shape es idéntico al contrato definido abajo (le falta `actions`, que se agrega cuando el agente decisor esté conectado).

**Segundo endpoint en vivo — todos los movimientos crudos, sin filtrar (para la vista de detalle y para que el frontend tenga con qué jugar libremente):**
```
GET https://qj0vumzrfa.execute-api.us-east-1.amazonaws.com/transactions?user_id=mia
```
Devuelve `{ transactions: [...], summary: {...} }` — cada transacción con `date`, `type` (deposit/purchase), `category`, `category_label`, `merchant_name`, `amount`, `signed_amount`, y `running_balance` ya calculado (para graficar balance en el tiempo sin re-derivar nada). `summary` trae totales y gasto por categoría. 62 movimientos reales de los 90 días de Mia.

**Tercer endpoint en vivo — el agente decisor completo, probado de punta a punta con escrituras reales en Nessie:**
```
POST https://qj0vumzrfa.execute-api.us-east-1.amazonaws.com/simulation/advance-day?user_id=mia
POST .../simulation/advance-day?user_id=mia&reset=true   ← reinicia la simulacion a Dia 0
```
Cada llamada avanza un checkpoint de la historia de Mia (Día 45 → 62 → 63 → 90) aplicando la política de riesgo real:

| Checkpoint | Qué pasa | ¿Se ejecuta solo? |
|---|---|---|
| Día 45 | Solo observación — reporta el score | N/A, no hay acción |
| Día 62 | Detecta la fuga de Gym Co | **No** — genera un `leak_detected` con `requires_confirmation: true` y la advertencia de riesgo contractual, no toca nada |
| Día 63 | Confirmación asumida → verificación → `PUT /bills` real en Nessie (`status: cancelled`) | Solo después de "confirmar", nunca antes |
| Día 90 | Verifica que el bill de arriba sí se detuvo (dependencia causal real) → `POST /withdrawals` + `POST /deposits` reales en Nessie | Autónomo — es reversible, no depende de terceros |

Probado en vivo: el bill queda `cancelled` de verdad en Nessie, aparecen el withdrawal y el deposit de $40 reales, y el score sube de 64 a **74** una vez resuelta la fuga — la causa→efecto es real, no simulada en el frontend.

Detalle completo de la historia simulada en [`/seed/README.md`](./seed/README.md).

## Frontend — qué construir

**Dos pantallas, no una, no muchas.** Una sola pantalla suena más segura pero le quita espacio a todo — el chat se ve apretado, el feed se ve chiquito, y se lee menos "producto real" (las apps bancarias de verdad, como la de Capital One con Eno, navegan entre dashboard y chat). El riesgo extra de una segunda pantalla es mínimo si el salto es siempre el mismo botón — se ensaya y ya no falla.

1. **Pantalla 1 — Dashboard (home).** Login/onboarding: mockeado o saltado, no le gastes tiempo. Aterrizas directo aquí.
2. **Pantalla 2 — Chat con el agente**, a pantalla completa (no un panel lateral apretado) — al estilo Eno real. Se llega por **un solo botón/ícono de chat siempre visible**, siempre el mismo destino — ese es todo el riesgo de navegación que se agrega.

Narrativa de demo que esto habilita: *"aquí está Mia hoy (pantalla 1) → le pica al chat, aquí confirmó lo del gimnasio (pantalla 2) → regresamos y miren cómo ya subió el score (pantalla 1 de nuevo)"*.

**Qué mostrar en el Dashboard (pantalla 1), en orden de importancia:**

1. **Score como número hero** — grande, arriba, con flecha de tendencia (↑/↓) y una frase de qué significa.
2. **Desglose del score en 4 barras** (no tabla) — regularidad de ingreso, ratio esencial/discrecional, recurrencia sana, colchón de liquidez. Esto es lo que prueba "no es caja negra".
3. **Feed de acciones del agente** — timeline, más reciente arriba. Esta es la sección más importante de toda la pantalla: es la prueba visual de que el agente actúa, no solo aconseja.
4. **Alertas activas** — tarjeta simple, solo lo que necesita atención ahora. Se resuelve → desaparece o se marca resuelta.
5. **Botón de chat** — siempre visible, lleva a la pantalla 2.

**Qué mostrar en el Chat (pantalla 2):** conversación completa con el agente — notificaciones proactivas, confirmaciones con contexto, explicaciones post-acción. Aquí sí puede respirar, no compite por espacio con el resto.

**Nice-to-have si alcanza el tiempo (en el Dashboard):** sparkline del score en el tiempo, una línea de "te alcanza para los próximos N días", una línea de proyección ("listo para un producto de crédito en ~5 semanas").

**Qué NO mostrar:** tabla completa de las 52 transacciones sembradas (si acaso, detrás de un "ver detalle" colapsado, nunca visible por default), pie chart de categorías con muchas rebanadas, métricas de vanidad (fecha de creación de cuenta, conteo total de transacciones). Antes de meter un dato nuevo, pregúntate: ¿esto prueba que el agente decide y actúa, o solo describe datos? Si es lo segundo, no va en la pantalla principal.

**Contrato de datos — empieza con esto hardcodeado, no esperes al backend:**

```json
{
  "score": {
    "value": 62,
    "trend": "up",
    "breakdown": [
      { "key": "income_regularity", "label": "Regularidad de ingreso", "weight": 35, "value": 70 },
      { "key": "essential_ratio", "label": "Ratio esencial/discrecional", "weight": 25, "value": 55 },
      { "key": "bill_health", "label": "Recurrencia sana", "weight": 20, "value": 40 },
      { "key": "liquidity_cushion", "label": "Colchón de liquidez", "weight": 20, "value": 75 }
    ]
  },
  "alerts": [
    { "id": "a1", "type": "leak", "severity": "high", "title": "Gym Co", "detail": "Sin actividad relacionada hace 60 días", "annual_cost": 480, "status": "detected" }
  ],
  "liquidity": { "days_covered": 12 },
  "projection": { "weeks_to_ready": 5, "product": "tarjeta secured" },
  "actions": [
    { "id": "t1", "date": "2026-08-25", "type": "leak_detected", "text": "Detecté que Gym Co ($40/mes) no tiene actividad relacionada hace 60 días" },
    { "id": "t2", "date": "2026-08-26", "type": "bill_stopped", "text": "Detuve el cargo de Gym Co, confirmaste que ya no lo usas", "amount": 40 },
    { "id": "t3", "date": "2026-09-08", "type": "savings_moved", "text": "Moví $40 a tu ahorro porque tu ingreso llegó antes y tu gasto esencial ya está cubierto", "amount": 40 }
  ]
}
```

**Endpoints que vas a consumir cuando el backend esté listo** (mismo shape que el mock de arriba):
- `GET /signals?customer_id=...` → el objeto completo de arriba
- `POST /chat/message { message }` → `{ reply, actions_taken }`
- `POST /simulation/advance-day` → `{ date, new_actions, score }` — el botón de "avanzar día"

Si el shape real cambia, avisa al resto del equipo antes de romperlo — es el contrato que todos están usando en paralelo.

**Endpoint real ya en vivo (usa este en lugar del mock cuando quieras):**
```
GET https://qj0vumzrfa.execute-api.us-east-1.amazonaws.com/signals?user_id=mia
```
Copia [`.env.example`](./.env.example) a `.env` — ahí está la URL como `VITE_API_BASE_URL`.

**Deploy ya disponible, sin esperar a nadie:** bucket de S3 con hosting estático ya configurado y probado.
```
aws s3 sync build/ s3://centinel-one-frontend --region us-east-1
```
URL en vivo: `http://centinel-one-frontend.s3-website-us-east-1.amazonaws.com`. CloudFront + dominio `.tech` se conectan hasta el final (ver sección de Arquitectura), esto es solo para ir viendo avances en una URL real desde ya.

## Estado actual

- [x] Definición de score, política de riesgo y arquitectura
- [x] Seed de ~90 días de historial simulado en Nessie
- [x] Motor de señales — desplegado como Lambda real + DynamoDB, endpoint `GET /signals` en vivo
- [x] Endpoint de datos crudos — `GET /transactions`, 62 movimientos con balance corriendo
- [x] Agente decisor — política de riesgo + verificación + escrituras reales a Nessie, probado de punta a punta (`POST /simulation/advance-day`)
- [x] Frontend conectado al backend real — el template inicial (`FundFlow`) no tenía ninguna conexión (confirmado en su propio README: "no se realizan movimientos reales ni se conecta al backend"). Se agregó `frontend/src/api.js` y se conectaron balance, transacciones, score y el agente (botón "Avanzar día" + feed) a los 3 endpoints en vivo.
- [ ] Chat conversacional real (lenguaje natural sobre el agente) — hoy el feed usa el texto fijo que ya genera `advance-day`, funciona para la demo pero no es un LLM respondiendo en el momento
- [ ] Ver [`PLAN.md`](./PLAN.md) para el plan de implementación completo
- [~] Dashboard — en progreso (frontend trabajando contra el contrato de datos mock)

## Track

Capital One Hackathon 2026 — Track 1: Consumer Financial Autonomy & Credit Building.
