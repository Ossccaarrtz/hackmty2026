# Datos sembrados — la historia de Ana

Este seed no son datos aleatorios: está armado a propósito para que el motor de señales tenga algo real que encontrar. Cubre 90 días (2026-06-14 a 2026-09-12) en el sandbox de Nessie, en MXN.

## El personaje

Ana, 19 años, segundo semestre de universidad. Su banco le dio una tarjeta-credencial al inscribirse (identificación + cuenta de débito, vía convenio banco-universidad) — la usó una vez para sacar efectivo y nunca más. Cero archivo en Buró de crédito.

## La cuenta

- `Ana - Cuenta universitaria` — cuenta principal, donde entra la mesada/pago de medio tiempo y salen los gastos.
- `Ana - Ahorro` — cuenta de ahorro, vacía al inicio; aquí es donde el agente movería dinero cuando detecte una ventana segura.

## El ingreso (8 depósitos, irregular a propósito)

Entre $1,150 y $2,100 MXN, cada 1–2 semanas — mesada de sus papás y pago de medio tiempo en una cafetería, nunca la misma fecha ni el mismo monto, para que la feature de "regularidad de ingreso" tenga varianza real que medir.

## El gasto (51 compras)

- **Renta de cuarto** — 3 pagos de $2,800 MXN, uno por mes. Gasto esencial, siempre puntual.
- **Cafetería** — ~18 compras, 2x por semana, $40–$130 MXN. Esencial.
- **Transporte (camión/ruta)** — ~12 compras, ~1.3x por semana, $15–$40 MXN. Esencial.
- **Plan celular (Telcel Plan)** — 3 pagos de $200 MXN, uno por mes — esta es la actividad que hace que el bill de Telcel Plan se lea como sano, no como fuga.
- **Discrecional** (café/cine) — 15 compras, $45–$180 MXN, repartidas al azar en los 90 días.

## Los dos bills recurrentes (el corazón del demo)

- **FitZone Campus — $250/mes, estado `recurring`.** Cero compras relacionadas con "FitZone Campus" en todo el historial. Esta es la fuga que el motor de señales debe encontrar solo: un cargo que sigue activo pero sin ninguna señal de uso real.
- **Telcel Plan — $200/mes, estado `recurring`, con 3 pagos reales registrados.** Este es el "control sano" — un cargo recurrente que sí tiene actividad relacionada, para probar que el detector no marca todo como sospechoso.

## Los números que debería reproducir el motor de señales

| | |
|---|---|
| Ingreso total | $12,650 MXN |
| Gasto total | $12,442 MXN |
| Balance real (ledger propio) | $208 MXN |
| Score final | 57/100 |
| Fuga detectable | FitZone Campus — ~$3,000/año sin uso (Telcel Plan NO debe marcarse) |
| Activación | 100/100, activa (compras recientes con la tarjeta del banco) |
| Wallet share | 100% (sin gasto declarado en efectivo/otra tarjeta todavía) |

Si el motor de señales calcula otra cosa, algo en el cálculo está mal — estos números son el ground truth, verificados en vivo contra la API real desplegada.

## IDs y ledger completo

Ver [`ana-seed-output.json`](./ana-seed-output.json) para los IDs de cuentas/merchants/bills en Nessie y el detalle de cada movimiento. El script que generó todo esto es [`ana-seed.js`](./ana-seed.js) — se puede volver a correr si hace falta resembrar (usa la misma API key del equipo).

## Nota histórica

La primera persona sembrada en este proyecto (antes del pivote a Spark) fue "Mia" — una freelancer con ingreso irregular, usada para validar el prototipo original ("Centinel One"). Sus datos y scripts de seed (`jarbis-seed.js`) se eliminaron del repo tras el pivote a Ana, para no dejar referencias a una persona que ya no es el cliente objetivo del proyecto.
