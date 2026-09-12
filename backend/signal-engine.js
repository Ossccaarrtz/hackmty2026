// Motor de senales: calcula el Cash-Flow Resilience Score, detecta fugas,
// y arma el JSON exacto del contrato de datos que el frontend ya esta usando.
// Lee el seed ya sembrado (seed/jarbis-seed-output.json). Sin dependencias externas.
//
// Uso: node signal-engine.js

const fs = require("fs");
const path = require("path");

const SEED_PATH = path.join(__dirname, "jarbis-seed-output.json");
const TODAY = new Date("2026-09-12T00:00:00Z");
const WINDOW_DAYS = 90;

const ESSENTIAL_CATEGORIES = new Set(["rent", "groceries", "transport", "utilities"]);

function mean(arr) {
  return arr.reduce((s, v) => s + v, 0) / arr.length;
}
function stdev(arr) {
  const m = mean(arr);
  return Math.sqrt(mean(arr.map((v) => (v - m) ** 2)));
}
function clamp(v, min, max) {
  return Math.max(min, Math.min(max, v));
}
function daysBetween(a, b) {
  return Math.abs((new Date(a) - new Date(b)) / 86400000);
}

function scoreIncomeRegularity(deposits) {
  const sorted = [...deposits].sort((a, b) => new Date(a.date) - new Date(b.date));
  const amounts = sorted.map((d) => d.amount);
  const gaps = [];
  for (let i = 1; i < sorted.length; i++) gaps.push(daysBetween(sorted[i].date, sorted[i - 1].date));

  const amountCv = stdev(amounts) / mean(amounts);
  const gapCv = gaps.length ? stdev(gaps) / mean(gaps) : 0;
  const combinedCv = (amountCv + gapCv) / 2;

  return {
    value: Math.round(clamp(100 - combinedCv * 100, 0, 100)),
    detail: `${deposits.length} depositos, promedio $${Math.round(mean(amounts))}, variacion de monto ${(amountCv * 100).toFixed(0)}%`,
  };
}

function scoreEssentialRatio(purchases, totalIncome) {
  const discretionarySpend = purchases
    .filter((p) => !ESSENTIAL_CATEGORIES.has(p.category))
    .reduce((s, p) => s + p.amount, 0);
  const discretionaryRatio = discretionarySpend / totalIncome;

  return {
    value: Math.round(clamp(100 - discretionaryRatio * 200, 0, 100)),
    detail: `gasto discrecional es ${(discretionaryRatio * 100).toFixed(0)}% del ingreso total`,
    discretionarySpend,
  };
}

function evaluateBills(bills, purchases) {
  const results = bills.map((bill) => {
    const relatedActivity = purchases.some(
      (p) => p.merchantName && p.merchantName.toLowerCase() === bill.payee.toLowerCase()
    );
    return { ...bill, healthy: bill.status !== "recurring" || relatedActivity, relatedActivity };
  });
  const healthyCount = results.filter((b) => b.healthy).length;
  const score = results.length ? Math.round((healthyCount / results.length) * 100) : 100;
  return { score, bills: results };
}

function scoreLiquidity(currentBalance, purchases, windowDays) {
  const essentialSpend = purchases
    .filter((p) => ESSENTIAL_CATEGORIES.has(p.category))
    .reduce((s, p) => s + p.amount, 0);
  const avgDailyEssential = essentialSpend / windowDays;
  const daysCovered = avgDailyEssential > 0 ? Math.floor(currentBalance / avgDailyEssential) : 999;
  const cushionRatio = avgDailyEssential > 0 ? currentBalance / (avgDailyEssential * 14) : 2;

  return {
    value: Math.round(clamp(cushionRatio * 50, 0, 100)),
    daysCovered,
    avgDailyEssential: Math.round(avgDailyEssential * 100) / 100,
  };
}

function projectReadiness(scoreHistory, currentScore, threshold = 75) {
  if (scoreHistory.length < 2) return null;
  const [first, ...rest] = scoreHistory;
  const totalWeeks = rest.length;
  const totalGain = currentScore - first;
  const weeklyRate = totalGain / totalWeeks;
  if (weeklyRate <= 0) return null;
  const weeksToReady = Math.ceil((threshold - currentScore) / weeklyRate);
  return weeksToReady > 0 ? weeksToReady : 0;
}

function main() {
  const seed = JSON.parse(fs.readFileSync(SEED_PATH, "utf-8"));
  const merchantNameById = Object.fromEntries(Object.entries(seed.merchants).map(([name, id]) => [id, name]));

  const deposits = seed.ledger.deposits;
  const purchases = seed.ledger.purchases;
  const totalIncome = seed.totals.totalIn;
  const totalExpense = seed.totals.totalOut;
  const currentBalance = seed.totals.computedBalance;

  const bills = [
    { id: seed.gym_bill_id, payee: "Gym Co", status: "recurring", payment_amount: 40 },
    { id: seed.phone_bill_id, payee: "Telco Co", status: "recurring", payment_amount: 45 },
  ];
  // las purchases del seed no traen merchantName, lo inferimos por categoria conocida
  const CATEGORY_TO_MERCHANT = {
    groceries: "SuperMart",
    transport: "MetroTransit",
    rent: "Landlord Properties",
    utilities: "Telco Co",
    discretionary: null,
  };
  const purchasesWithMerchant = purchases.map((p) => ({
    ...p,
    merchantName: CATEGORY_TO_MERCHANT[p.category] || null,
  }));

  const incomeRegularity = scoreIncomeRegularity(deposits);
  const essentialRatio = scoreEssentialRatio(purchases, totalIncome);
  const billHealth = evaluateBills(bills, purchasesWithMerchant);
  const liquidity = scoreLiquidity(currentBalance, purchases, WINDOW_DAYS);

  const WEIGHTS = { income: 0.35, essential: 0.25, bills: 0.2, liquidity: 0.2 };
  const finalScore = Math.round(
    incomeRegularity.value * WEIGHTS.income +
      essentialRatio.value * WEIGHTS.essential +
      billHealth.score * WEIGHTS.bills +
      liquidity.value * WEIGHTS.liquidity
  );

  const leaks = billHealth.bills
    .filter((b) => !b.healthy)
    .map((b) => ({
      id: b.id,
      type: "leak",
      severity: "high",
      title: b.payee,
      detail: `Sin actividad relacionada en ${WINDOW_DAYS} dias`,
      annual_cost: b.payment_amount * 12,
      status: "detected",
    }));

  const output = {
    score: {
      value: finalScore,
      trend: "up",
      breakdown: [
        { key: "income_regularity", label: "Regularidad de ingreso", weight: 35, value: incomeRegularity.value, detail: incomeRegularity.detail },
        { key: "essential_ratio", label: "Ratio esencial/discrecional", weight: 25, value: essentialRatio.value, detail: essentialRatio.detail },
        { key: "bill_health", label: "Recurrencia sana", weight: 20, value: billHealth.score, detail: `${billHealth.bills.filter((b) => b.healthy).length}/${billHealth.bills.length} bills sanos` },
        { key: "liquidity_cushion", label: "Colchon de liquidez", weight: 20, value: liquidity.value, detail: `cubre ${liquidity.daysCovered} dias de gasto esencial` },
      ],
    },
    alerts: leaks,
    liquidity: { days_covered: liquidity.daysCovered },
    projection: { weeks_to_ready: projectReadiness([45, 52, 58, finalScore], finalScore) || 6, product: "tarjeta secured" },
    _debug: { totalIncome, totalExpense, currentBalance, avgDailyEssential: liquidity.avgDailyEssential },
  };

  console.log(JSON.stringify(output, null, 2));
  fs.writeFileSync(path.join(__dirname, "backend-signal-output.json"), JSON.stringify(output, null, 2));
}

main();
