# Datos sembrados — la historia de Mia

Este seed no son datos aleatorios: está armado a propósito para que el motor de señales tenga algo real que encontrar. Cubre 90 días (2026-06-14 a 2026-09-12) en el sandbox de Nessie.

## El personaje

Mia, 24 años, ingreso por proyecto (freelance). Cero archivo en Buró de crédito.

## La cuenta

- `Mia Checking` — cuenta principal, donde entra el ingreso y salen los gastos.
- `Mia Ahorro` — cuenta de ahorro, vacía al inicio; aquí es donde el agente movería dinero cuando detecte una ventana segura.

## El ingreso (8 depósitos, irregular a propósito)

Entre $300 y $720, cada 1–2 semanas — nunca la misma fecha ni el mismo monto, para que la feature de "regularidad de ingreso" tenga varianza real que medir.

## El gasto (52 compras)

- **Renta** — 3 pagos de $650, uno por mes. Gasto esencial, siempre puntual.
- **Súper** — ~18 compras, 2x por semana, $35–$90. Esencial.
- **Transporte** — ~12 compras, ~1.3x por semana, $15–$30. Esencial.
- **Discrecional** (restaurantes/entretenimiento) — 15 compras, $18–$60, repartidas al azar en los 90 días.

## Los dos bills recurrentes (el corazón del demo)

- **Gym Co — $40/mes, estado `recurring`.** Cero compras relacionadas con "Gym Co" en todo el historial. Esta es la fuga que el motor de señales debe encontrar solo: un cargo que sigue activo pero sin ninguna señal de uso real.
- **Telco Co — $45/mes, estado `recurring`.** Este es el "control sano" — un cargo recurrente que no debería marcarse como fuga, para probar que el detector no marca todo como sospechoso.

## Los números que debería reproducir el motor de señales

| | |
|---|---|
| Ingreso total | $4,520 |
| Gasto total | $3,879 |
| Balance real (ledger propio) | $641 |
| Fuga detectable | Gym Co — ~$480/año sin uso |

Si el motor de señales calcula otra cosa, algo en el cálculo está mal — estos números son el ground truth.

## IDs y ledger completo

Ver [`jarbis-seed-output.json`](./jarbis-seed-output.json) para los IDs de cuentas/merchants/bills en Nessie y el detalle de cada movimiento. El script que generó todo esto es [`jarbis-seed.js`](./jarbis-seed.js) — se puede volver a correr si hace falta resembrar (usa la misma API key del equipo).
