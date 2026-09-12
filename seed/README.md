# Datos sembrados — la historia de Mia

Este seed no son datos aleatorios: está armado a propósito para que el motor de señales tenga algo real que encontrar. Cubre 90 días (2026-06-14 a 2026-09-12) en el sandbox de Nessie.

## El personaje

Mia, 24 años, ingreso por proyecto (freelance). Cero archivo en Buró de crédito.

## La cuenta

- `Mia Checking` — cuenta principal, donde entra el ingreso y salen los gastos.
- `Mia Ahorro` — cuenta de ahorro, vacía al inicio; aquí es donde el agente movería dinero cuando detecte una ventana segura.

## El ingreso (8 depósitos, irregular a propósito)

Entre $300 y $720, cada 1–2 semanas — nunca la misma fecha ni el mismo monto, para que la feature de "regularidad de ingreso" tenga varianza real que medir.

## El gasto (55 compras)

- **Renta** — 3 pagos de $650, uno por mes. Gasto esencial, siempre puntual.
- **Súper** — ~18 compras, 2x por semana, $35–$90. Esencial.
- **Transporte** — ~12 compras, ~1.3x por semana, $15–$30. Esencial.
- **Teléfono (Telco Co)** — 3 pagos de $45, uno por mes (3 de cada mes) — esta es la actividad que hace que el bill de Telco Co se lea como sano, no como fuga.
- **Discrecional** (restaurantes/entretenimiento) — 15 compras, $18–$60, repartidas al azar en los 90 días.

## Los dos bills recurrentes (el corazón del demo)

- **Gym Co — $40/mes, estado `recurring`.** Cero compras relacionadas con "Gym Co" en todo el historial. Esta es la fuga que el motor de señales debe encontrar solo: un cargo que sigue activo pero sin ninguna señal de uso real.
- **Telco Co — $45/mes, estado `recurring`, con 3 pagos reales registrados.** Este es el "control sano" — un cargo recurrente que sí tiene actividad relacionada, para probar que el detector no marca todo como sospechoso.

> **Nota de una corrección real:** en la primera versión del seed, Telco Co no tenía ninguna compra relacionada y el motor de señales lo marcaba como fuga también — el motor estaba haciendo bien su trabajo, el seed estaba incompleto. Se corrigió agregando los 3 pagos. Queda como recordatorio de por qué hay que correr el motor contra los datos reales y no solo asumir que el seed está bien.

## Los números que debería reproducir el motor de señales

| | |
|---|---|
| Ingreso total | $4,520 |
| Gasto total | $4,014 |
| Balance real (ledger propio) | $506 |
| Score final | 65/100 |
| Fuga detectable | Gym Co — ~$480/año sin uso (Telco Co NO debe marcarse) |

Si el motor de señales calcula otra cosa, algo en el cálculo está mal — estos números son el ground truth. Confirmado corriendo [`/backend/signal-engine.js`](../backend/signal-engine.js).

## IDs y ledger completo

Ver [`jarbis-seed-output.json`](./jarbis-seed-output.json) para los IDs de cuentas/merchants/bills en Nessie y el detalle de cada movimiento. El script que generó todo esto es [`jarbis-seed.js`](./jarbis-seed.js) — se puede volver a correr si hace falta resembrar (usa la misma API key del equipo).
