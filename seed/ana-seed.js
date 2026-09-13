// Seed de ~90 dias de historial financiero para "Ana" (persona nueva del pivote a
// Spark: estudiante universitaria, tarjeta-credencial bancaria, montos en MXN).
// Cuenta y customer creados en esta misma sesion, con la misma llave que usa Mia
// (Ana es un persona propio, no un tercero -- distinto del "empleador" de la demo
// de nomina, que si usa una cuenta/llave ajena a proposito).
// Ejecutar: node ana-seed.js

const KEY = "551cf0a83cad5495b4e5e713c2948dda";
const BASE = "https://api.nessieisreal.com";

const CUSTOMER_ID = "cf8635eb-36e1-4987-8c13-1a977e228130"; // Ana Martinez
const CHECKING_ID = "3303b959-15c4-4a5d-aeea-1e0503712d37";
const SAVINGS_ID = "5616be1c-84c4-4e37-9736-9028d5d444a0";

const TODAY = new Date("2026-09-12T00:00:00Z"); // mismo "hoy" simulado que usa Mia
const WINDOW_DAYS = 90;

function isoDate(d) {
  return d.toISOString().slice(0, 10);
}
function daysAgo(n) {
  const d = new Date(TODAY);
  d.setUTCDate(d.getUTCDate() - n);
  return d;
}
function randInt(min, max) {
  return Math.floor(min + Math.random() * (max - min + 1));
}
function pick(arr) {
  return arr[randInt(0, arr.length - 1)];
}

async function api(method, path, body) {
  const url = `${BASE}${path}${path.includes("?") ? "&" : "?"}key=${KEY}`;
  const res = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let json;
  try { json = JSON.parse(text); } catch (e) { json = text; }
  if (!res.ok && !(json && json.code === 202)) {
    console.warn(`  [WARN] ${method} ${path} -> ${res.status}:`, JSON.stringify(json).slice(0, 200));
  }
  return json;
}

async function createMerchant(name, category) {
  const r = await api("POST", "/merchants", { name, category });
  return r.objectCreated ? r.objectCreated._id : null;
}

async function createPurchase(accountId, merchantId, amount, date, description) {
  return api("POST", `/accounts/${accountId}/purchases`, {
    merchant_id: merchantId,
    medium: "balance",
    amount,
    purchase_date: isoDate(date),
    status: "completed",
    description,
  });
}

async function createDeposit(accountId, amount, date, description) {
  return api("POST", `/accounts/${accountId}/deposits`, {
    medium: "balance",
    transaction_date: isoDate(date),
    status: "completed",
    amount,
    description,
  });
}

async function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function main() {
  const ledger = { deposits: [], purchases: [] };
  console.log("=== 1. Creando merchants (contexto de campus, MXN) ===");
  const merchants = {};
  const merchantDefs = [
    ["Casa Renta Estudiantil", "rent"],
    ["Cafeteria Central", "groceries"],
    ["Ruta Universidad", "transport"],
    ["Telcel Plan", "utilities"],
    ["Cafe Central", "restaurant"],
    ["CineExtra", "entertainment"],
  ];
  for (const [name, category] of merchantDefs) {
    const id = await createMerchant(name, category);
    merchants[name] = id;
    console.log(`  ${name} (${category}) -> ${id}`);
    await sleep(150);
  }

  console.log("\n=== 2. Creando bills recurrentes ===");
  const gymBill = await api("POST", `/accounts/${CHECKING_ID}/bills`, {
    status: "recurring",
    payee: "FitZone Campus",
    nickname: "Membresia gimnasio",
    payment_date: isoDate(daysAgo(5)),
    recurring_date: 5,
    payment_amount: 250,
  });
  const gymBillId = gymBill.objectCreated && gymBill.objectCreated._id;
  console.log("  FitZone Campus bill -> recurring (fuga a detectar, sin actividad de compra relacionada) ->", gymBillId);

  const phoneBill = await api("POST", `/accounts/${CHECKING_ID}/bills`, {
    status: "recurring",
    payee: "Telcel Plan",
    nickname: "Plan celular",
    payment_date: isoDate(daysAgo(3)),
    recurring_date: 3,
    payment_amount: 200,
  });
  const phoneBillId = phoneBill.objectCreated && phoneBill.objectCreated._id;
  console.log("  Telcel Plan bill -> recurring (sano, se paga cada mes) ->", phoneBillId);

  console.log("\n=== 3. Sembrando ingreso irregular (mesada + trabajo de medio tiempo, MXN) ===");
  const incomeSchedule = [88, 74, 67, 53, 39, 32, 18, 4]; // dias atras, mismo patron de cadencia que Mia
  const incomeAmounts = [1750, 2100, 2600, 1950, 2750, 1650, 2450, 3000];
  const incomeDescriptions = [
    "Mesada de mis papas", "Mesada de mis papas", "Pago medio tiempo - cafeteria",
    "Mesada de mis papas", "Pago medio tiempo - cafeteria", "Mesada de mis papas",
    "Pago medio tiempo - cafeteria", "Mesada de mis papas",
  ];
  for (let i = 0; i < incomeSchedule.length; i++) {
    const date = daysAgo(incomeSchedule[i]);
    const amount = incomeAmounts[i];
    await createDeposit(CHECKING_ID, amount, date, incomeDescriptions[i]);
    ledger.deposits.push({ date: isoDate(date), amount });
    console.log(`  ${isoDate(date)}  +$${amount} MXN  (${incomeDescriptions[i]})`);
    await sleep(150);
  }

  console.log("\n=== 4. Sembrando renta de cuarto (3 pagos, MXN) ===");
  for (const daysBack of [85, 55, 25]) {
    const date = daysAgo(daysBack);
    await createPurchase(CHECKING_ID, merchants["Casa Renta Estudiantil"], 2800, date, "Renta de cuarto");
    ledger.purchases.push({ date: isoDate(date), amount: 2800, category: "rent" });
    console.log(`  ${isoDate(date)}  -$2800 MXN  (renta de cuarto)`);
    await sleep(150);
  }

  console.log("\n=== 5. Sembrando cafeteria/despensa (2x/semana, ~9 semanas, MXN) ===");
  for (let w = 0; w < 9; w++) {
    for (let k = 0; k < 2; k++) {
      const daysBack = w * 7 + randInt(0, 6);
      if (daysBack >= WINDOW_DAYS) continue;
      const date = daysAgo(daysBack);
      const amount = randInt(40, 130);
      await createPurchase(CHECKING_ID, merchants["Cafeteria Central"], amount, date, "Cafeteria - comida");
      ledger.purchases.push({ date: isoDate(date), amount, category: "groceries" });
      await sleep(120);
    }
  }
  console.log("  listo (~18 compras de cafeteria)");

  console.log("\n=== 6. Sembrando transporte (camion/ruta, ~1.3x/semana, MXN) ===");
  for (let w = 0; w < 9; w++) {
    const hits = randInt(1, 2);
    for (let k = 0; k < hits; k++) {
      const daysBack = w * 7 + randInt(0, 6);
      if (daysBack >= WINDOW_DAYS) continue;
      const date = daysAgo(daysBack);
      const amount = randInt(15, 40);
      await createPurchase(CHECKING_ID, merchants["Ruta Universidad"], amount, date, "Transporte - camion");
      ledger.purchases.push({ date: isoDate(date), amount, category: "transport" });
      await sleep(120);
    }
  }
  console.log("  listo (~12 compras de transporte)");

  console.log("\n=== 7. Sembrando gasto discrecional (cafe/cine, MXN) ===");
  for (let i = 0; i < 15; i++) {
    const daysBack = randInt(0, WINDOW_DAYS - 1);
    const date = daysAgo(daysBack);
    const merchant = pick(["Cafe Central", "CineExtra"]);
    const amount = randInt(45, 180);
    await createPurchase(CHECKING_ID, merchants[merchant], amount, date, `Gasto discrecional - ${merchant}`);
    ledger.purchases.push({ date: isoDate(date), amount, category: "discretionary" });
    await sleep(120);
  }
  console.log("  listo (15 compras discrecionales)");

  console.log("\n=== 8. Sembrando plan celular (bill sano, con actividad relacionada real, MXN) ===");
  for (const daysBack of [83, 53, 23]) {
    const date = daysAgo(daysBack);
    await createPurchase(CHECKING_ID, merchants["Telcel Plan"], 200, date, "Pago plan celular");
    ledger.purchases.push({ date: isoDate(date), amount: 200, category: "utilities" });
    console.log(`  ${isoDate(date)}  -$200 MXN  (plan celular)`);
    await sleep(150);
  }

  const totalIn = ledger.deposits.reduce((s, d) => s + d.amount, 0);
  const totalOut = ledger.purchases.reduce((s, p) => s + p.amount, 0);
  const computedBalance = totalIn - totalOut;

  console.log("\n=== RESUMEN ===");
  console.log(`Deposits: ${ledger.deposits.length}  |  Total ingreso: $${totalIn} MXN`);
  console.log(`Purchases: ${ledger.purchases.length}  |  Total gasto: $${totalOut} MXN`);
  console.log(`Balance calculado (nuestro ledger, NO el de Nessie): $${computedBalance} MXN`);

  const fs = await import("fs");
  fs.writeFileSync(
    "ana-seed-output.json",
    JSON.stringify(
      {
        customer_id: CUSTOMER_ID,
        checking_id: CHECKING_ID,
        savings_id: SAVINGS_ID,
        merchants,
        gym_bill_id: gymBillId,
        phone_bill_id: phoneBillId,
        window_days: WINDOW_DAYS,
        today: isoDate(TODAY),
        ledger,
        totals: { totalIn, totalOut, computedBalance },
      },
      null,
      2
    )
  );
  console.log("\nGuardado: ana-seed-output.json (ids + ledger completo para cargar a DynamoDB)");
}

main().catch((e) => {
  console.error("Error fatal:", e);
  process.exit(1);
});
