// Seed de ~90 dias de historial financiero para "Mia" en el sandbox de Nessie.
// Reusa el customer/accounts/merchant de gimnasio ya creados durante las pruebas de esta sesion.
// Ejecutar: node jarbis-seed.js

const KEY = "551cf0a83cad5495b4e5e713c2948dda";
const BASE = "https://api.nessieisreal.com";

const CUSTOMER_ID = "01b63288-df01-4444-a1ce-8b902a1a5694"; // Mia Test
const CHECKING_ID = "3cbe83c6-e844-48b3-b86a-627b8a6e3028";
const SAVINGS_ID = "f9428a58-dbc4-49b3-9105-e69460a56a9a";
const GYM_MERCHANT_ID = "efbeb6c2-a6fd-4b24-bf1f-6d2ef2dc7646"; // "Gym Co", category "gym"
const GYM_BILL_ID = "6811f340-284b-48c9-b588-f1f8c6469a9e"; // bill ya creado para el gym

const TODAY = new Date("2026-09-12T00:00:00Z");
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
  console.log("=== 1. Creando merchants esenciales/discrecionales ===");
  const merchants = {};
  const merchantDefs = [
    ["Landlord Properties", "rent"],
    ["SuperMart", "groceries"],
    ["MetroTransit", "transport"],
    ["Tasty Bites", "restaurant"],
    ["CineMax", "entertainment"],
    ["Telco Co", "utilities"],
  ];
  for (const [name, category] of merchantDefs) {
    const id = await createMerchant(name, category);
    merchants[name] = id;
    console.log(`  ${name} (${category}) -> ${id}`);
    await sleep(150);
  }
  merchants["Gym Co"] = GYM_MERCHANT_ID;

  console.log("\n=== 2. Reactivando bills recurrentes (estado inicial: activos) ===");
  await api("PUT", `/bills/${GYM_BILL_ID}`, {
    status: "recurring",
    payee: "Gym Co",
    nickname: "Gym membership",
    payment_date: isoDate(daysAgo(5)),
    recurring_date: 5,
    payment_amount: 40,
  });
  console.log("  Gym Co bill -> recurring (fuga a detectar, sin actividad de compra relacionada)");

  const phoneBill = await api("POST", `/accounts/${CHECKING_ID}/bills`, {
    status: "recurring",
    payee: "Telco Co",
    nickname: "Phone plan",
    payment_date: isoDate(daysAgo(3)),
    recurring_date: 3,
    payment_amount: 45,
  });
  console.log("  Telco Co bill -> recurring (sano, se paga cada mes) ->", phoneBill.objectCreated && phoneBill.objectCreated._id);

  console.log("\n=== 3. Sembrando ingreso irregular (freelance, cada 1-2 semanas) ===");
  const incomeSchedule = [88, 74, 67, 53, 39, 32, 18, 4]; // dias atras
  const incomeAmounts = [620, 380, 710, 450, 690, 300, 650, 720];
  for (let i = 0; i < incomeSchedule.length; i++) {
    const date = daysAgo(incomeSchedule[i]);
    const amount = incomeAmounts[i];
    await createDeposit(CHECKING_ID, amount, date, "Pago freelance - proyecto cliente");
    ledger.deposits.push({ date: isoDate(date), amount });
    console.log(`  ${isoDate(date)}  +$${amount}  (deposit freelance)`);
    await sleep(150);
  }

  console.log("\n=== 4. Sembrando renta mensual (3 pagos) ===");
  for (const daysBack of [85, 55, 25]) {
    const date = daysAgo(daysBack);
    await createPurchase(CHECKING_ID, merchants["Landlord Properties"], 650, date, "Renta mensual");
    ledger.purchases.push({ date: isoDate(date), amount: 650, category: "rent" });
    console.log(`  ${isoDate(date)}  -$650  (renta)`);
    await sleep(150);
  }

  console.log("\n=== 5. Sembrando super (2x/semana, ~9 semanas) ===");
  for (let w = 0; w < 9; w++) {
    for (let k = 0; k < 2; k++) {
      const daysBack = w * 7 + randInt(0, 6);
      if (daysBack >= WINDOW_DAYS) continue;
      const date = daysAgo(daysBack);
      const amount = randInt(35, 90);
      await createPurchase(CHECKING_ID, merchants["SuperMart"], amount, date, "Super - despensa");
      ledger.purchases.push({ date: isoDate(date), amount, category: "groceries" });
      await sleep(120);
    }
  }
  console.log("  listo (~18 compras de super)");

  console.log("\n=== 6. Sembrando transporte (~1.3x/semana) ===");
  for (let w = 0; w < 9; w++) {
    const hits = randInt(1, 2);
    for (let k = 0; k < hits; k++) {
      const daysBack = w * 7 + randInt(0, 6);
      if (daysBack >= WINDOW_DAYS) continue;
      const date = daysAgo(daysBack);
      const amount = randInt(15, 30);
      await createPurchase(CHECKING_ID, merchants["MetroTransit"], amount, date, "Transporte");
      ledger.purchases.push({ date: isoDate(date), amount, category: "transport" });
      await sleep(120);
    }
  }
  console.log("  listo (~12 compras de transporte)");

  console.log("\n=== 7. Sembrando gasto discrecional (restaurantes/entretenimiento) ===");
  for (let i = 0; i < 15; i++) {
    const daysBack = randInt(0, WINDOW_DAYS - 1);
    const date = daysAgo(daysBack);
    const merchant = pick(["Tasty Bites", "CineMax"]);
    const amount = randInt(18, 60);
    await createPurchase(CHECKING_ID, merchants[merchant], amount, date, `Gasto discrecional - ${merchant}`);
    ledger.purchases.push({ date: isoDate(date), amount, category: "discretionary" });
    await sleep(120);
  }
  console.log("  listo (15 compras discrecionales)");

  const totalIn = ledger.deposits.reduce((s, d) => s + d.amount, 0);
  const totalOut = ledger.purchases.reduce((s, p) => s + p.amount, 0);
  const computedBalance = totalIn - totalOut;

  console.log("\n=== RESUMEN ===");
  console.log(`Deposits: ${ledger.deposits.length}  |  Total ingreso: $${totalIn}`);
  console.log(`Purchases: ${ledger.purchases.length}  |  Total gasto: $${totalOut}`);
  console.log(`Balance calculado (nuestro ledger, NO el de Nessie): $${computedBalance}`);
  console.log(`\nOJO: el campo 'balance' de la cuenta en Nessie seguira mostrando su valor viejo`);
  console.log(`(bug ya confirmado). El motor de senales debe usar este ledger propio, no GET /accounts.`);

  const fs = await import("fs");
  fs.writeFileSync(
    "jarbis-seed-output.json",
    JSON.stringify(
      {
        customer_id: CUSTOMER_ID,
        checking_id: CHECKING_ID,
        savings_id: SAVINGS_ID,
        merchants,
        gym_bill_id: GYM_BILL_ID,
        phone_bill_id: phoneBill.objectCreated && phoneBill.objectCreated._id,
        window_days: WINDOW_DAYS,
        today: isoDate(TODAY),
        ledger,
        totals: { totalIn, totalOut, computedBalance },
      },
      null,
      2
    )
  );
  console.log("\nGuardado: jarbis-seed-output.json (ids + ledger completo para el motor de senales)");
}

main().catch((e) => {
  console.error("Error fatal:", e);
  process.exit(1);
});
