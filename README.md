# Jarbis Financiero — HackMTY 2026 (Reto Capital One)

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
| **Jarbis Financiero** | Score sin Buró + ejecuta acciones reales con verificación | — |

## Arquitectura

```
NESSIE API (datos) → Motor de señales (score + detección) → Agente decisor (política de riesgo + verificación) → interfaz (chat/dashboard) + escritura de vuelta a Nessie
```

Se reutiliza la arquitectura de un proyecto previo del equipo ("Jarbis": asistente personal con bot de Telegram, tool-calling, verificación anti-alucinación, backend Lambda + DynamoDB, dashboard React).

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

## Estado actual

- [x] Definición de score, política de riesgo y arquitectura
- [x] Seed de ~90 días de historial simulado en Nessie (8 depósitos, 52 compras, 2 bills recurrentes, una fuga deliberada en el gimnasio)
- [ ] Motor de señales (cálculo del score + detección de fugas)
- [ ] Conexión del agente decisor a los endpoints de Nessie
- [ ] Dashboard y timeline de acciones para la demo

## Track

Capital One Hackathon 2026 — Track 1: Consumer Financial Autonomy & Credit Building.
