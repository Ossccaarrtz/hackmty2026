import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ChartPie, MessageCircle, Wallet, RefreshCw, X, ShieldAlert, ArrowUpRight, ArrowDownLeft, ArrowUp, ArrowDown, Minus, Play, RotateCcw, ChevronRight, ChevronLeft, Search, Send, User, Lock, Eye, EyeOff, Download, PiggyBank, CalendarDays, Gift } from 'lucide-react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { api, sessionKey } from './api.js';
import { appendCheckpoint, appendChatExchange, emptySession, normalizeData } from './data.js';
import './styles.css';
import CategorySpending from './CategorySpending.jsx';

const money = value => Number.isFinite(value) ? new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value) : '—';
// Debe calzar con EXTERNAL_EXPENSE_CATEGORIES/EXTERNAL_CATEGORY_LABELS en
// agent_actions.py -- son las mismas 5 categorias que usa el resto del
// motor de senales, a proposito no se inventa una taxonomia nueva.
// Hardcodeado a proposito -- estos son ejemplos de beneficios reales que
// tendria la tarjeta-credencial universitaria de Kivo, no datos que vengan
// del backend. Elegidos para reforzar el propio pitch del proyecto (historial
// sin Buro, alertas en tiempo real) en vez de una lista generica.
// Fotos genericas de Unsplash (licencia libre, sin atribucion requerida) --
// revisadas una por una antes de elegirlas, ninguna es contenido editorial
// con derechos de un tercero.
const BENEFITS = [
  { photo: 'https://images.unsplash.com/photo-1556742049-0cfed4f6a45d?w=500&q=70&auto=format&fit=crop', title: 'Sin anualidad', detail: 'Nunca pagas por tener la tarjeta, la uses mucho o poco.' },
  { photo: 'https://images.unsplash.com/photo-1563013544-824ae1b704d3?w=500&q=70&auto=format&fit=crop', title: 'Historial sin Buró', detail: 'Cada mes de uso responsable construye tu historial crediticio, aunque sea tu primera tarjeta.' },
  { photo: 'https://images.unsplash.com/photo-1512428559087-560fa5ceab42?w=500&q=70&auto=format&fit=crop', title: 'Alertas en tiempo real', detail: 'Te avisamos de cada movimiento al instante -- un cargo nunca te toma por sorpresa.' },
];
const BUDGET_CATEGORY_OPTIONS = [
  { value: 'rent', label: 'Renta' }, { value: 'groceries', label: 'Comida/Despensa' },
  { value: 'transport', label: 'Transporte' }, { value: 'utilities', label: 'Servicios' },
  { value: 'discretionary', label: 'Discrecional' },
];
const dateLabel = date => new Intl.DateTimeFormat('es-MX', { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(new Date(`${date}T00:00:00Z`));
const monthLabel = yearMonth => new Intl.DateTimeFormat('es-MX', { month: 'long', year: 'numeric', timeZone: 'UTC' }).format(new Date(`${yearMonth}-01T00:00:00Z`));
const WEEKDAY_LABELS = ['Dom', 'Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb'];
function addDaysISO(iso, days) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}
function shiftYearMonth(yearMonth, offset) {
  const [y, m] = yearMonth.split('-').map(Number);
  const d = new Date(Date.UTC(y, m - 1 + offset, 1));
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
}
function buildCalendarGrid(yearMonth) {
  const [y, m] = yearMonth.split('-').map(Number);
  const daysInMonth = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const firstWeekday = new Date(Date.UTC(y, m - 1, 1)).getUTCDay();
  const cells = Array(firstWeekday).fill(null);
  for (let day = 1; day <= daysInMonth; day++) cells.push(`${yearMonth}-${String(day).padStart(2, '0')}`);
  return cells;
}
const PENDING_TYPES = ['leak_detected', 'anomaly_pause'];
// El backend devuelve label + un "detail" tecnico por factor (ej. "gasto discrecional
// es 34% del ingreso total") pensado para depurar el motor de senales, no para una
// estudiante de primera vez en banca. Esto traduce cada factor (por su `key`, estable)
// a una explicacion en espanol simple; el detalle tecnico se conserva como nota chica.
const SCORE_FACTOR_EXPLAINERS = {
  income_regularity: '¿Te llega dinero de forma constante y en fechas parecidas? Entre más regular sea tu ingreso, más sube este número.',
  essential_ratio: '¿Qué tanto de tu dinero se va en cosas que sí o sí necesitas (comida, transporte, renta) contra gastos opcionales (streaming, salidas)? Gastar menos en lo opcional lo sube.',
  bill_health: '¿Los cargos fijos que tienes (como una membresía) los sigues usando de verdad? Si detectamos uno que ya no usas, este número baja -- por eso te avisamos antes de que siga cobrándote.',
  liquidity_cushion: '¿Cuántos días podrías cubrir tus gastos esenciales si hoy dejaras de recibir dinero? Más días cubiertos, más colchón para un imprevisto.',
};
// El feed de acciones trae `action.type` en snake_case tal
// cual lo usa el backend internamente (nombre de tool o tipo de evento) -- sin esto se
// veian literales como "verification_blocked" o "move_to_savings" en la UI de Ana.
const ACTION_TYPE_LABELS = {
  score_check: 'Revisión de tu cuenta', info: 'Aviso', error: 'Error',
  leak_detected: 'Fuga detectada', anomaly_pause: 'Pausa por actividad inusual',
  bill_stopped: 'Cargo detenido', verification_blocked: 'Acción bloqueada por seguridad',
  chat_rejected: 'Acción no realizada', savings_moved: 'Dinero movido a ahorro',
  stop_subscription: 'Suscripción detenida', confirm_stop_bill: 'Cargo detenido',
  move_to_savings: 'Dinero movido a ahorro', release_savings_buffer: 'Ahorro liberado',
  set_category_budget: 'Meta de presupuesto guardada', log_external_expense: 'Gasto externo registrado',
  set_monthly_budget: 'Meta de gasto actualizada', create_goal: 'Meta de ahorro guardada',
  log_goal_contribution: 'Aporte a meta registrado', get_smart_allocation: 'Reparto inteligente consultado',
};
const actionTypeLabel = type => ACTION_TYPE_LABELS[type] || type.replaceAll('_', ' ');
// "Revisar" en una alerta llevaba al chat en blanco -- la estudiante tenia que
// releer la alerta y reescribirsela a Kivo de memoria. Esto arma una pregunta
// tipo "que pasaria si..." con los datos que la alerta ya trae, precargada en
// el input (sin auto-enviar) para que Kivo explique el escenario antes de que
// se decida a actuar.
function buildAlertPrompt(alert) {
  if (alert.type === 'leak') return `¿Qué pasaría si dejo de pagar ${alert.title}? Me cuesta ${money(alert.monthly_amount)}/mes (${money(alert.annual_cost)} al año) y no lo he usado en un rato.`;
  if (alert.type === 'liquidity_warning') return `${alert.detail} ¿Qué pasaría si no hago nada? ¿Qué me recomiendas para subir mi colchón sin quedarme sin dinero para lo esencial?`;
  if (alert.type === 'activation_warning') return `${alert.detail} ¿Qué pasaría si sigo sin usar mi tarjeta?`;
  return `Ayúdame a entender esto: ${alert.title} — ${alert.detail}. ¿Qué pasaría si no hago nada al respecto?`;
}

// El chat manda markdown ligero (negritas con **, listas con *) que Gemini
// genera de forma natural -- sin esto se veian los asteriscos literales en
// vez de negritas reales. Deliberadamente no se usa una libreria de markdown
// completa (react-markdown, etc.): solo negrita y listas, que es todo lo que
// el prompt del backend realmente produce.
function renderInlineBold(text, keyPrefix) {
  return text.split(/(\*\*[^*]+\*\*)/g).filter(Boolean).map((part, i) =>
    part.startsWith('**') && part.endsWith('**')
      ? <strong key={`${keyPrefix}-${i}`}>{part.slice(2, -2)}</strong>
      : <React.Fragment key={`${keyPrefix}-${i}`}>{part}</React.Fragment>
  );
}
function renderChatText(text) {
  const lines = text.split(/\n+/).map(line => line.trim()).filter(Boolean);
  const blocks = [];
  lines.forEach((line, i) => {
    const bulletMatch = line.match(/^[*-]\s+(.*)/);
    const inline = renderInlineBold(bulletMatch ? bulletMatch[1] : line, `l${i}`);
    const last = blocks.at(-1);
    if (bulletMatch && last?.type === 'ul') last.items.push(inline);
    else if (bulletMatch) blocks.push({ type: 'ul', items: [inline] });
    else blocks.push({ type: 'p', content: inline });
  });
  return blocks.map((block, i) => block.type === 'ul'
    ? <ul className="chat-bullet-list" key={i}>{block.items.map((item, j) => <li key={j}>{item}</li>)}</ul>
    : <p key={i}>{block.content}</p>);
}
function TypingIndicator() {
  return <div className="chat-bubble assistant typing-indicator" aria-label="Kivo está escribiendo"><span /><span /><span /></div>;
}
function readSession() {
  try { const saved = JSON.parse(sessionStorage.getItem(sessionKey)); if (Array.isArray(saved?.feed) && Array.isArray(saved?.history)) return { ...emptySession(), ...saved }; } catch { /* Storage is optional. */ }
  return emptySession();
}
const BUDGET_KEY = `${sessionKey}:monthly-budget`;
function readMonthlyBudget() {
  try { const value = Number(localStorage.getItem(BUDGET_KEY)); return Number.isFinite(value) && value > 0 ? value : null; } catch { return null; }
}
function budgetTone(percent) {
  if (percent >= 100) return 'over';
  if (percent >= 75) return 'warn';
  return 'good';
}
function budgetMessage(tone, percent, overBy) {
  if (tone === 'over') return `Ya te pasaste de tu meta por ${money(overBy)}.`;
  if (tone === 'warn') return `Cuidado, ya usaste ${Math.round(percent)}% de tu meta.`;
  return `Vas bien -- llevas ${Math.round(percent)}% de tu meta.`;
}
// Cada punto es un registro real (una transaccion, un checkpoint) -- nunca un
// dia de calendario interpolado, por eso el eje X no lleva fechas parejas.
// El hover (crosshair + tooltip) existe para que esa fecha real de cada punto
// sea legible sin tener que adivinar la posicion en el eje.
function LineChart({ values, dates, label, formatValue = value => value.toFixed(0) }) {
  const [hoverIndex, setHoverIndex] = useState(null);
  const svgRef = useRef(null);
  if (values.length < 2) return <p className="chart-empty">Se necesitan al menos dos registros para mostrar la evolución.</p>;
  const min = Math.min(...values), max = Math.max(...values), range = max - min || 1;
  const x = i => 10 + i * 380 / (values.length - 1);
  const y = value => 150 - (value - min) * 130 / range;
  const points = values.map((value, i) => `${x(i)},${y(value)}`).join(' ');
  const areaPoints = `${x(0)},150 ${points} ${x(values.length - 1)},150`;
  const zeroY = min < 0 && max > 0 ? y(0) : null;

  function updateHoverFromClientX(clientX) {
    const rect = svgRef.current.getBoundingClientRect();
    if (!rect.width) return;
    const relX = (clientX - rect.left) / rect.width * 400;
    let nearest = 0, bestDist = Infinity;
    values.forEach((_, i) => { const dist = Math.abs(x(i) - relX); if (dist < bestDist) { bestDist = dist; nearest = i; } });
    setHoverIndex(nearest);
  }

  return <div className="real-chart-wrap" onMouseLeave={() => setHoverIndex(null)}>
    <svg ref={svgRef} className="real-chart" viewBox="0 0 400 170" role="img" aria-label={label}
      onMouseMove={event => updateHoverFromClientX(event.clientX)}
      onTouchStart={event => updateHoverFromClientX(event.touches[0].clientX)}
      onTouchMove={event => updateHoverFromClientX(event.touches[0].clientX)}
      onTouchEnd={() => setHoverIndex(null)}>
      {zeroY != null && <><line x1="10" x2="390" y1={zeroY} y2={zeroY} className="chart-zero-line" /><text x="10" y={zeroY - 4} className="chart-zero-label">0</text></>}
      <polygon points={areaPoints} className="chart-area" />
      <polyline points={points} fill="none" className="chart-line" strokeLinejoin="round" strokeLinecap="round" />
      {hoverIndex != null && <>
        <line x1={x(hoverIndex)} x2={x(hoverIndex)} y1="6" y2="150" className="chart-crosshair" />
        <circle cx={x(hoverIndex)} cy={y(values[hoverIndex])} r="5" className="chart-hover-dot" />
      </>}
      <text x="10" y="168" className="chart-axis-label">{formatValue(min)}</text>
      <text x="390" y="16" textAnchor="end" className="chart-axis-label">{formatValue(max)}</text>
    </svg>
    {hoverIndex != null && <div className="chart-tooltip" style={{ left: `${x(hoverIndex) / 400 * 100}%` }}>
      <strong>{formatValue(values[hoverIndex])}</strong>
      {dates && <span>{dateLabel(dates[hoverIndex])}</span>}
    </div>}
  </div>;
}
function TrendBadge({ trend }) {
  if (trend === 'up') return <span className="trend-badge trend-up"><ArrowUp size={14} /> Subiendo</span>;
  if (trend === 'down') return <span className="trend-badge trend-down"><ArrowDown size={14} /> Bajando</span>;
  return <span className="trend-badge trend-flat"><Minus size={14} /> Estable</span>;
}
// Traduce anomalia/alertas/tendencia a una sola frase humana para el hero del home,
// en vez de que la estudiante tenga que interpretar un pill tecnico de "anomalia".
function scoreStatusFeedback(signals) {
  if (signals.anomaly?.detected) return { tone: 'alert', text: 'Notamos algo distinto a tu patrón habitual — revisa las alertas de abajo.' };
  if (signals.alerts?.length > 0) return { tone: 'alert', text: 'Hay algo que necesita tu atención.' };
  if (signals.score.trend === 'down') return { tone: 'alert', text: 'Tu score bajó un poco — vale la pena revisar qué cambió.' };
  return { tone: 'good', text: 'Vas bien, sigue así.' };
}
const MOTION_EASE = [0.22, 1, 0.36, 1];
function buildDashboardVariants(reduce) {
  return {
    container: { hidden: {}, visible: { transition: reduce ? {} : { delayChildren: 0.15, staggerChildren: 0.08 } } },
    item: {
      hidden: reduce ? { opacity: 1 } : { opacity: 0, y: 16, scale: 0.98 },
      visible: { opacity: 1, y: 0, scale: 1, transition: { duration: reduce ? 0 : 0.5, ease: MOTION_EASE } },
    },
  };
}
const AUTH_KEY = 'spark:authed';
function readAuthed() {
  try {
    if (localStorage.getItem(AUTH_KEY) === 'true') return true;
    return sessionStorage.getItem(AUTH_KEY) === 'true';
  } catch { return false; }
}
const FAQS = [
  { q: '¿Qué es el Cash-Flow Resilience Score?', a: 'Un indicador propio (0-100) de qué tan resiliente es tu flujo de efectivo, calculado a partir de tu comportamiento real: regularidad de ingreso, ratio esencial/discrecional, recurrencia de bills y colchón de liquidez. No es un score de Buró y no requiere historial crediticio previo.' },
  { q: '¿Cómo detecta Kivo una fuga de dinero?', a: 'Compara tus bills recurrentes contra actividad relacionada real (por ejemplo, un cargo de gimnasio sin visitas asociadas). Si no encuentra esa actividad por un periodo prolongado, la marca como posible fuga y te avisa antes de hacer nada.' },
  { q: '¿Kivo puede mover mi dinero sin avisarme?', a: 'Solo en un caso: mover dinero a tu propio ahorro, porque es reversible y no involucra a terceros. Detener un cargo y las metas de presupuesto NUNCA mueven dinero -- son solo la vista y los recordatorios de Kivo; detener un cargo recurrente siempre pide tu confirmación explícita primero, y te advierte si podría tener implicaciones contractuales con el comercio.' },
  { q: '¿Qué pasa con mis datos financieros?', a: 'El modelo de lenguaje nunca ve tu historial crudo de transacciones -- solo señales ya derivadas (ej. "score=57, fuga detectada: FitZone Campus"). Toda la demo corre sobre el sandbox de Capital One Nessie, no datos reales.' },
  { q: '¿Qué significa "Sin anomalías"?', a: 'Kivo compara tu actividad reciente contra tu propio historial. Si detecta un patrón fuera de lo normal, pausa cualquier acción autónoma y te pide confirmar antes de continuar, en vez de actuar solo.' },
];
function FaqWidget() {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(null);
  useEffect(() => {
    if (!open) return;
    const onKey = event => event.key === 'Escape' && setOpen(false);
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);
  return <>
    <button className={`faq-fab ${open ? 'is-open' : ''}`} aria-label={open ? 'Cerrar preguntas frecuentes' : 'Preguntas frecuentes'} onClick={() => setOpen(v => !v)}>
      {open ? <X size={22} /> : <img src="/kivo-icon.png" alt="" />}
    </button>
    {open && <section className="faq-panel" role="dialog" aria-label="Preguntas frecuentes">
      <header className="faq-panel-header"><span>Preguntas frecuentes</span></header>
      <div className="faq-list">
        {FAQS.map((item, index) => <div className={`faq-item ${activeIndex === index ? 'is-active' : ''}`} key={item.q}>
          <button className="faq-question" onClick={() => setActiveIndex(activeIndex === index ? null : index)}>
            {item.q}<ChevronRight size={16} />
          </button>
          {activeIndex === index && <p className="faq-answer">{item.a}</p>}
        </div>)}
      </div>
    </section>}
  </>;
}
function PrivacyPolicy() {
  return <>
    <h2 id="privacy-title">Aviso de privacidad (demo)</h2>
    <p>Kivo es un proyecto para el Hackathon de Capital One (Track 1) — no procesa datos financieros reales; todos los movimientos vienen del sandbox de Capital One Nessie.</p>
    <p>El modelo de lenguaje (Gemini) nunca recibe tu historial crudo de transacciones, solo señales ya derivadas por nuestro motor (por ejemplo, "score=57, fuga detectada: FitZone Campus"). Toda acción que mueve dinero pasa primero por una verificación que confirma que coincide con lo que el motor de señales ya calculó, antes de escribir en Nessie.</p>
    <p className="muted-copy">Para un producto real, esto requeriría un acuerdo de procesamiento de datos (DPA) con el proveedor del modelo y cumplimiento de GLBA — el mismo proceso que sigue cualquier institución financiera al usar un proveedor de nube.</p>
  </>;
}
function Footer() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const onKey = event => event.key === 'Escape' && setOpen(false);
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);
  return <>
    <footer className="app-footer">
      <span>© 2026 Kivo — Capital One Hackathon</span>
      <button className="footer-link" onClick={() => setOpen(true)}>Aviso de privacidad</button>
    </footer>
    {open && <div className="modal-overlay" onClick={() => setOpen(false)}>
      <section className="modal glass" role="dialog" aria-modal="true" aria-labelledby="privacy-title" onClick={event => event.stopPropagation()}>
        <button className="close-modal icon-button" aria-label="Cerrar" onClick={() => setOpen(false)}><X /></button>
        <PrivacyPolicy />
      </section>
    </div>}
  </>;
}
function Login({ onLogin }) {
  const [email, setEmail] = useState('ana@kivo.mx');
  const [password, setPassword] = useState('demo1234');
  const [showPassword, setShowPassword] = useState(false);
  const [remember, setRemember] = useState(false);
  function submit(event) {
    event.preventDefault();
    onLogin(remember);
  }
  return <div className="login-page">
    <div className="login-center">
      <form className="login-card glass" onSubmit={submit}>
        <img src="/kivo-logo.png" alt="Kivo" className="login-logo" />
        <h1 className="login-title">Iniciar sesión</h1>
        <p className="login-tagline">Agente de autonomía financiera · Track 1, Capital One Hackathon 2026</p>
        <label className="login-field">
          <span>Nombre de usuario</span>
          <span className="login-input-wrap"><User size={17} /><input type="email" autoComplete="username" value={email} onChange={event => setEmail(event.target.value)} required /></span>
        </label>
        <label className="login-field">
          <span>Contraseña</span>
          <span className="login-input-wrap">
            <Lock size={17} />
            <input type={showPassword ? 'text' : 'password'} autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} required />
            <button type="button" className="login-toggle-visibility" aria-label={showPassword ? 'Ocultar contraseña' : 'Mostrar contraseña'} onClick={() => setShowPassword(v => !v)}>
              {showPassword ? <EyeOff size={17} /> : <Eye size={17} />}
            </button>
          </span>
        </label>
        <label className="login-remember"><input type="checkbox" checked={remember} onChange={event => setRemember(event.target.checked)} /> Recordarme</label>
        <button className="login-cta black-button" type="submit">Iniciar sesión</button>
        <button type="button" className="text-button login-forgot">¿Olvidaste tu usuario o contraseña?</button>
        <p className="login-footnote">Acceso de demostración — cuenta de Ana precargada.</p>
      </form>
    </div>
  </div>;
}

function App() {
  const shouldReduceMotion = useReducedMotion();
  const { container: dashboardContainer, item: dashboardItem } = buildDashboardVariants(shouldReduceMotion);
  const hoverLift = shouldReduceMotion ? undefined : { y: -4 };
  const [authed, setAuthed] = useState(readAuthed);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [chatBusy, setChatBusy] = useState(false); // separado de `busy` para no disparar el indicador de escritura en acciones que no son del chat (ej. "avanzar dia")
  const [operationError, setOperationError] = useState('');
  const [uncertain, setUncertain] = useState(false);
  const [session, setSession] = useState(readSession);
  const [page, setPage] = useState('home');
  const [modal, setModal] = useState(null);
  const [query, setQuery] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [notice, setNotice] = useState('');
  const [calendarExpenses, setCalendarExpenses] = useState(null);
  const [calendarLoading, setCalendarLoading] = useState(false);
  const [calendarError, setCalendarError] = useState('');
  const [calendarMonthOffset, setCalendarMonthOffset] = useState(0);
  const [selectedCalendarDay, setSelectedCalendarDay] = useState(null);
  const [statementMonth, setStatementMonth] = useState('');
  const [budgetData, setBudgetData] = useState(null);
  const [budgetLoading, setBudgetLoading] = useState(false);
  const [budgetError, setBudgetError] = useState('');
  const [budgetNotice, setBudgetNotice] = useState('');
  const [budgetBusy, setBudgetBusy] = useState(false);
  const [newBudgetCategory, setNewBudgetCategory] = useState('');
  const [newBudgetTarget, setNewBudgetTarget] = useState('');
  const [newBudgetLabel, setNewBudgetLabel] = useState('');
  const [payrollBusy, setPayrollBusy] = useState(false);
  const [smartAllocation, setSmartAllocation] = useState(null);
  const [smartAllocationLoading, setSmartAllocationLoading] = useState(false);
  const [smartAllocationError, setSmartAllocationError] = useState('');
  const [contributionGoal, setContributionGoal] = useState('');
  const [contributionAmount, setContributionAmount] = useState('');
  const [chatInput, setChatInput] = useState('');
  // localStorage es solo el valor optimista mientras carga -- en cuanto refresh()
  // trae `monthly_budget` del backend (fijado aqui mismo o por chat via
  // set_monthly_budget), ese valor manda. El % usado siempre es de transacciones reales.
  const [monthlyBudget, setMonthlyBudget] = useState(readMonthlyBudget);
  // Una vez que el backend confirma un valor (fijado por chat o desde otro
  // dispositivo), la edicion local se deshabilita -- de lo contrario un cambio
  // hecho aqui se revertiria solo en el siguiente refresh() sin explicacion.
  const [budgetSource, setBudgetSource] = useState('local');
  const [editingBudget, setEditingBudget] = useState(false);
  const [budgetInput, setBudgetInput] = useState('');
  const locked = useRef(false), requestId = useRef(0), closeRef = useRef(null);
  const chatEndRef = useRef(null);
  const signals = data?.signals;
  const transactions = data?.transactions || [];
  const categoryOptions = [...new Set(transactions.map(tx => tx.category).filter(Boolean))]
    .map(key => ({ key, label: transactions.find(tx => tx.category === key)?.category_label || key }))
    .sort((a, b) => a.label.localeCompare(b.label));
  const filtered = transactions.slice().reverse()
    .filter(tx => `${tx.name} ${tx.category_label}`.toLowerCase().includes(query.toLowerCase()))
    .filter(tx => !categoryFilter || tx.category === categoryFilter)
    .filter(tx => !dateFrom || tx.date >= dateFrom)
    .filter(tx => !dateTo || tx.date <= dateTo);
  const statementMonths = [...new Set(transactions.map(tx => tx.date.slice(0, 7)))].sort().reverse();
  const activeStatementMonth = statementMonth || statementMonths[0] || '';
  const statementTransactions = transactions.filter(tx => tx.date.slice(0, 7) === activeStatementMonth).sort((a, b) => a.date.localeCompare(b.date));
  const statementIncome = statementTransactions.filter(tx => tx.signed_amount > 0).reduce((sum, tx) => sum + tx.signed_amount, 0);
  // Bank-only, igual que running_balance del backend: un gasto en efectivo/otra
  // tarjeta nunca salio de esta cuenta, asi que no debe restarse del saldo que
  // el estado de cuenta reconcilia (saldo inicial + ingresos - gastos = saldo final).
  const statementExpense = statementTransactions.filter(tx => tx.signed_amount < 0 && (tx.source || 'bank') === 'bank').reduce((sum, tx) => sum - tx.signed_amount, 0);
  const statementBreakdown = Object.entries(
    statementTransactions.filter(tx => tx.signed_amount < 0 && !['savings_transfer'].includes(tx.category) && !tx.category?.startsWith('envelope:'))
      .reduce((acc, tx) => { acc[tx.category_label] = (acc[tx.category_label] || 0) + Math.abs(tx.signed_amount); return acc; }, {})
  ).sort((a, b) => b[1] - a[1]);
  // No se deriva del propio signed_amount de la primera transaccion del mes
  // (fragil desde que running_balance dejo de moverse con gasto externo) --
  // se busca el running_balance real justo antes de que empezara el mes.
  const firstStatementIndex = statementTransactions.length ? transactions.findIndex(tx => tx.id === statementTransactions[0].id) : -1;
  const statementOpening = statementTransactions.length ? (firstStatementIndex > 0 ? transactions[firstStatementIndex - 1].running_balance : 0) : null;
  const statementClosing = statementTransactions.length ? statementTransactions.at(-1).running_balance : null;
  const editingBudgetCategory = newBudgetCategory && budgetData?.budget.categories.some(cat => cat.category === newBudgetCategory);
  // "Mes actual" = el mes mas reciente con movimientos en el ledger, no la fecha real
  // del dispositivo -- la simulacion puede estar parada en cualquier dia sembrado.
  // Deliberadamente independiente de `statementMonth` (el selector de Estado de
  // cuenta): cambiar de mes ahi no debe mover la meta de gasto del home.
  const currentMonthKey = statementMonths[0] || '';
  const currentMonthExpense = transactions
    .filter(tx => tx.date.slice(0, 7) === currentMonthKey && tx.signed_amount < 0 && tx.category !== 'savings_transfer' && !tx.category?.startsWith('envelope:'))
    .reduce((sum, tx) => sum - tx.signed_amount, 0);
  const budgetPercent = monthlyBudget ? (currentMonthExpense / monthlyBudget) * 100 : 0;
  const calendarExpensesByDate = {};
  (calendarExpenses || []).forEach(item => {
    if (!calendarExpensesByDate[item.expected_date]) calendarExpensesByDate[item.expected_date] = [];
    calendarExpensesByDate[item.expected_date].push(item);
  });
  // "Hoy" segun el backend (nunca el reloj real del dispositivo, ver signal_engine.
  // resolve_reference_date) se deriva de cualquier item devuelto -- expected_date
  // menos days_until siempre da esa misma fecha de referencia.
  const calendarReferenceDate = calendarExpenses?.length
    ? addDaysISO(calendarExpenses[0].expected_date, -calendarExpenses[0].days_until)
    : (statementMonths[0] ? `${statementMonths[0]}-01` : null);
  const calendarReferenceYearMonth = calendarReferenceDate ? calendarReferenceDate.slice(0, 7) : null;
  const calendarDisplayedYearMonth = calendarReferenceYearMonth ? shiftYearMonth(calendarReferenceYearMonth, calendarMonthOffset) : null;
  const calendarCells = calendarDisplayedYearMonth ? buildCalendarGrid(calendarDisplayedYearMonth) : [];
  const selectedCalendarExpenses = selectedCalendarDay ? (calendarExpensesByDate[selectedCalendarDay] || []) : [];
  async function loadCalendar() {
    setCalendarLoading(true);
    setCalendarError('');
    try {
      // Ventana amplia solo para esta vista -- el home usa el default corto (5
      // dias) para que "Lo que necesita tu atencion" no se llene de ruido lejano.
      const result = await api.getSignals({ upcoming_days: 40 });
      setCalendarExpenses(result?.upcoming_expenses || []);
    } catch (err) { setCalendarError(err.message); }
    finally { setCalendarLoading(false); }
  }
  async function loadBudget() {
    setBudgetLoading(true);
    setBudgetError('');
    try {
      const result = await api.getBudget();
      setBudgetData(result);
      if (result?.monthly_budget?.amount != null) { setMonthlyBudget(Number(result.monthly_budget.amount)); setBudgetSource('backend'); }
    } catch (err) { setBudgetError(err.message); }
    finally { setBudgetLoading(false); }
  }
  async function submitCategoryBudget(event) {
    event.preventDefault();
    if (budgetBusy || !newBudgetCategory) return;
    setBudgetBusy(true);
    setBudgetNotice('');
    setBudgetError('');
    try {
      const result = await api.setCategoryBudget(newBudgetCategory, newBudgetTarget, newBudgetLabel);
      setBudgetNotice(result.message || 'Meta guardada.');
      setNewBudgetCategory(''); setNewBudgetTarget(''); setNewBudgetLabel('');
      await loadBudget();
    } catch (err) { setBudgetError(err.message); }
    finally { setBudgetBusy(false); }
  }
  async function simulatePayroll() {
    if (payrollBusy) return;
    setPayrollBusy(true);
    setBudgetNotice('');
    setBudgetError('');
    try {
      const result = await api.simulateThirdPartyPayroll(1500, 'Estudio Creativo');
      setBudgetNotice(`${result.message} Ese saldo ya lo puede usar el reparto inteligente de abajo.`);
      await refresh();
    } catch (err) { setBudgetError(err.message); }
    finally { setPayrollBusy(false); }
  }
  async function loadSmartAllocation() {
    setSmartAllocationLoading(true);
    setSmartAllocationError('');
    try { setSmartAllocation((await api.getSmartAllocation()).smart_allocation); }
    catch (err) { setSmartAllocationError(err.message); }
    finally { setSmartAllocationLoading(false); }
  }
  async function submitGoalContribution(event) {
    event.preventDefault();
    if (budgetBusy || !contributionGoal || !contributionAmount) return;
    setBudgetBusy(true);
    setBudgetNotice('');
    setBudgetError('');
    try {
      const result = await api.logGoalContribution(contributionGoal, contributionAmount);
      setBudgetNotice(result.message || 'Aporte registrado.');
      setContributionGoal(''); setContributionAmount('');
      await loadBudget();
    } catch (err) { setBudgetError(err.message); }
    finally { setBudgetBusy(false); }
  }
  function submitBudget(event) {
    event.preventDefault();
    const value = Number(budgetInput);
    if (!Number.isFinite(value) || value <= 0) return;
    setMonthlyBudget(value);
    setBudgetSource('local');
    setEditingBudget(false);
    api.setMonthlyBudget(value).catch(() => { /* se reintenta solo en el siguiente refresh() */ });
  }
  async function refresh() {
    const id = ++requestId.current;
    setLoading(true);
    try {
      const [s, t, budget] = await Promise.all([
        api.getSignals(),
        api.getTransactions(),
        api.getBudget().catch(() => null), // si la meta de gasto no carga, no tumba el resto del dashboard
      ]);
      const next = normalizeData(s, t);
      if (id !== requestId.current) return;
      setData(next);
      // Fijada por chat (set_monthly_budget) o desde otro dispositivo -- el backend
      // manda sobre el valor local en cuanto hay uno guardado ahi.
      if (budget?.monthly_budget?.amount != null) { setMonthlyBudget(Number(budget.monthly_budget.amount)); setBudgetSource('backend'); }
      setError('');
    } catch (err) { if (id === requestId.current) setError(err.message); }
    finally { if (id === requestId.current) setLoading(false); }
  }
  useEffect(() => { refresh(); return () => { requestId.current++; }; }, []);
  useEffect(() => { if (page === 'calendar') { setSelectedCalendarDay(null); setCalendarMonthOffset(0); loadCalendar(); } }, [page]);
  useEffect(() => { if (page === 'envelopes') { setBudgetNotice(''); loadBudget(); } }, [page]);
  useEffect(() => { try { sessionStorage.setItem(sessionKey, JSON.stringify(session)); } catch { /* Storage is optional. */ } }, [session]);
  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: shouldReduceMotion ? 'auto' : 'smooth', block: 'end' }); }, [session.chatLog, chatBusy]);
  useEffect(() => { try { sessionStorage.setItem(AUTH_KEY, authed ? 'true' : 'false'); } catch { /* Storage is optional. */ } }, [authed]);
  useEffect(() => { try { if (monthlyBudget != null) localStorage.setItem(BUDGET_KEY, String(monthlyBudget)); else localStorage.removeItem(BUDGET_KEY); } catch { /* Storage is optional. */ } }, [monthlyBudget]);
  useEffect(() => {
    if (!modal) return;
    const previous = document.activeElement;
    closeRef.current?.focus();
    const onKey = event => {
      if (event.key === 'Escape') setModal(null);
      if (event.key !== 'Tab') return;
      const elements = Array.from(document.querySelector('[role="dialog"]').querySelectorAll('button:not(:disabled), input, select, a[href]'));
      const first = elements[0], last = elements.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('keydown', onKey); previous?.focus(); };
  }, [modal]);

  async function mutate(kind) {
    if (locked.current) return;
    locked.current = true; setBusy(true); setOperationError(''); setModal(null); setNotice('');
    try {
      const result = kind === 'reset' ? await api.resetSimulation() : await api.advanceDay();
      if (kind === 'reset') {
        if (!result.reset) throw new Error('El backend no confirmó el reinicio.');
        setSession(emptySession()); setUncertain(false);
        setNotice('El backend confirmó el reinicio. Los movimientos anteriores de Nessie no se deshacen.');
      } else {
        const next = appendCheckpoint(session, result);
        setSession(next);
        setNotice(result.done ? result.message : `${result.label}: respuesta recibida del agente.`);
      }
      await refresh();
    } catch (err) {
      setUncertain(true);
      setOperationError(`${err.message} La operación podría haberse ejecutado. No se reintentará automáticamente; verifica el estado del sandbox antes de continuar.`);
    } finally { locked.current = false; setBusy(false); }
  }

  async function sendChat(event) {
    event.preventDefault();
    const text = chatInput.trim();
    if (!text || locked.current) return;
    locked.current = true; setBusy(true); setChatBusy(true); setOperationError('');
    setChatInput('');
    const baseSession = session; // snapshot de antes del mensaje optimista, para reconstruir el estado final sin duplicarlo
    setSession(s => ({ ...s, chatLog: [...s.chatLog, { id: `chat-pending-${Date.now()}`, role: 'user', text }] }));
    try {
      const result = await api.sendChatMessage(text);
      setSession(appendChatExchange(baseSession, text, result));
      await refresh(); // cualquier accion real que el chat haya ejecutado ya debe reflejarse en signals/transactions
    } catch (err) {
      setSession(s => ({ ...s, chatLog: [...s.chatLog, { id: `chat-err-${Date.now()}`, role: 'assistant', text: `No pude enviar tu mensaje: ${err.message}` }] }));
    } finally { locked.current = false; setBusy(false); setChatBusy(false); }
  }

  const controls = <div className="agent-controls"><button className="black-button small" disabled={busy || loading || !!error || !data || session.done || uncertain} onClick={() => setModal('advance')}><Play size={15} />{busy ? 'Procesando…' : session.done ? 'Demo completada' : 'Avanzar día'}</button><button className="outline-button small" disabled={busy || loading} onClick={() => setModal('reset')}><RotateCcw size={15} /> Reiniciar</button></div>;
  const feed = <div className="agent-feed">{session.feed.length ? session.feed.map(action => <article className={`agent-feed-item ${['error', 'verification_blocked', 'chat_rejected'].includes(action.type) ? 'action-error' : PENDING_TYPES.includes(action.type) || action.requires_confirmation ? 'action-pending' : ''}`} key={action.id}><span>{action.date && dateLabel(action.date)} · {actionTypeLabel(action.type)}</span><p>{action.text}</p></article>) : <p className="empty">Todavía no hay acciones recibidas en esta sesión. Avanza la simulación o escríbele a Kivo para ver sus respuestas.</p>}</div>;
  const chatTranscript = <div className="chat-transcript">{session.chatLog.length ? session.chatLog.map(m => <div className={`chat-bubble ${m.role}`} key={m.id}>{renderChatText(m.text)}</div>) : <p className="empty">Escríbele a Kivo: puede revisar tu score, detener una suscripción marcada como fuga, mover dinero a tu ahorro, o liberar parte de tu ahorro si esta semana te entró poco.</p>}{chatBusy && <TypingIndicator />}<div ref={chatEndRef} /></div>;

  if (!authed) return <Login onLogin={remember => { if (remember) { try { localStorage.setItem(AUTH_KEY, 'true'); } catch { /* Storage is optional. */ } } setAuthed(true); }} />;

  const isFullPage = page !== 'home';
  const backToHome = <button className="pill" onClick={() => setPage('home')}>Volver al inicio</button>;

  return <>
  <motion.main className={`dashboard connected-dashboard ${isFullPage ? 'full-page-layout page-focused' : ''}`} variants={dashboardContainer} initial="hidden" animate="visible">
    <motion.aside className="sidebar" aria-label="Navegación principal" variants={dashboardItem}><nav>{[[ChartPie, 'Inicio', 'home'], [MessageCircle, 'Chat con Kivo', 'chat'], [Wallet, 'Movimientos', 'transactions'], [CalendarDays, 'Calendario de gastos', 'calendar'], [PiggyBank, 'Gastos', 'envelopes'], [Gift, 'Beneficios', 'benefits']].map(([Icon, label, destination]) => <button className={`nav-button ${page === destination ? 'active' : ''}`} key={destination} aria-label={label} title={label} onClick={() => setPage(destination)}><Icon size={23} /></button>)}</nav><div className="sidebar-bottom"><button className="user-avatar" aria-label="Perfil de Ana" onClick={() => setModal('profile')}>A</button></div></motion.aside>
    <section className="main-column">
      <motion.header className="page-header" variants={dashboardItem}><div><div className="page-brand"><img src="/kivo-logo.png" alt="" className="page-brand-logo" /><h1 className="sr-only">Kivo</h1></div><p>Hola, Ana. Tu progreso financiero, en un solo lugar.</p></div><button className="pill" disabled={loading || busy} aria-label="Actualizar datos" onClick={refresh}><RefreshCw size={16} /> {loading ? 'Cargando…' : 'Actualizar'}</button></motion.header>
      {error && <div className="error-banner" role="alert">{error} <button disabled={loading || busy} onClick={refresh}>Reintentar lectura</button></div>}
      {operationError && <div className="error-banner" role="alert">{operationError}</div>}
      {notice && <p className="operation-notice" role="status">{notice}</p>}
      <AnimatePresence mode="popLayout" initial={false}>
        {page === 'chat' && <motion.section className="glass chat-panel" key="chat-panel"
          initial={{ opacity: 0, x: shouldReduceMotion ? 0 : 32 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: shouldReduceMotion ? 0 : 32 }}
          transition={{ duration: shouldReduceMotion ? 0 : 0.45, ease: MOTION_EASE }}>
          <header className="card-heading"><h2>Conversación con Kivo</h2><button className="pill" onClick={() => setPage('home')}>Volver al inicio</button></header>
          {chatTranscript}
          <form className="chat-form" onSubmit={sendChat}>
            <input aria-label="Mensaje para Kivo" placeholder="Ej. ¿cómo va mi score? / cancela FitZone Campus / mueve 20 a mi ahorro" value={chatInput} onChange={event => setChatInput(event.target.value)} disabled={busy} />
            <button className="black-button" type="submit" disabled={busy || !chatInput.trim()} aria-label="Enviar mensaje"><Send size={16} /></button>
          </form>
          <div className="chat-feed-section"><h3>Acciones recientes</h3>{feed}</div>
        </motion.section>}
        {page === 'transactions' && <motion.section className="glass page-panel transactions-page statement-printable" key="transactions-page"
          initial={{ opacity: 0, x: shouldReduceMotion ? 0 : 32 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: shouldReduceMotion ? 0 : 32 }}
          transition={{ duration: shouldReduceMotion ? 0 : 0.45, ease: MOTION_EASE }}>
          <div className="statement-header">
            <div><h2>Movimientos</h2><p className="muted-copy">{transactions.length} movimientos · Saldo: {money(data?.balance)}</p></div>
            <div className="statement-header-actions">
              <select aria-label="Filtrar movimientos por mes" value={activeStatementMonth} onChange={event => { const ym = event.target.value; setStatementMonth(ym); setDateFrom(`${ym}-01`); setDateTo(`${ym}-${String(new Date(Number(ym.slice(0, 4)), Number(ym.slice(5, 7)), 0).getDate()).padStart(2, '0')}`); }}>
                {statementMonths.map(ym => <option key={ym} value={ym}>{monthLabel(ym)}</option>)}
              </select>
              <button className="black-button small" onClick={() => window.print()}><Download size={15} /> Descargar PDF</button>
              {backToHome}
            </div>
          </div>
          {statementTransactions.length > 0 && <div className="statement-summary">
            <div className="statement-summary-item"><span>Saldo inicial del mes</span><strong>{money(statementOpening)}</strong></div>
            <div className="statement-summary-item"><span>Ingresos del mes</span><strong className="inflow">+{money(statementIncome)}</strong></div>
            <div className="statement-summary-item"><span>Gastos del mes</span><strong>-{money(statementExpense)}</strong></div>
            <div className="statement-summary-item"><span>Saldo final del mes</span><strong>{money(statementClosing)}</strong></div>
          </div>}
          <div className="transactions-charts">
            <div><h3 className="chart-block-title">Balance histórico</h3><p className="muted-copy">Tu saldo bancario después de cada movimiento real, en orden -- no es un promedio ni una proyección. Pasa el cursor sobre la línea para ver el saldo exacto en cada fecha.</p><LineChart values={transactions.map(tx => tx.running_balance)} dates={transactions.map(tx => tx.date)} formatValue={money} label="Balance histórico calculado por el backend" /></div>
            <CategorySpending summary={data?.summary} loading={loading} />
          </div>
          {statementBreakdown.length > 0 && <>
            <h3>En qué se fue el dinero en {monthLabel(activeStatementMonth)}</h3>
            {statementBreakdown.map(([label, amount]) => <div className="detail-line" key={label}><span>{label}</span><strong>{money(amount)}</strong></div>)}
          </>}
          <label className="ledger-search"><Search size={18} /><input aria-label="Buscar movimientos" placeholder="Buscar comercio o categoría" value={query} onChange={event => setQuery(event.target.value)} /></label>
          <div className="ledger-filters">
            <label className="ledger-filter"><span>Categoría</span><select aria-label="Filtrar por categoría" value={categoryFilter} onChange={event => setCategoryFilter(event.target.value)}><option value="">Todas</option>{categoryOptions.map(opt => <option key={opt.key} value={opt.key}>{opt.label}</option>)}</select></label>
            <label className="ledger-filter"><span>Desde</span><input type="date" aria-label="Fecha desde" value={dateFrom} onChange={event => setDateFrom(event.target.value)} /></label>
            <label className="ledger-filter"><span>Hasta</span><input type="date" aria-label="Fecha hasta" value={dateTo} onChange={event => setDateTo(event.target.value)} /></label>
            {(categoryFilter || dateFrom || dateTo) && <button type="button" className="text-button ledger-filter-clear" onClick={() => { setCategoryFilter(''); setDateFrom(''); setDateTo(''); }}>Limpiar filtros</button>}
          </div>
          <p className="muted-copy ledger-filter-count">{filtered.length} de {transactions.length} movimientos</p>
          {filtered.map(tx => <div className="detail-line" key={tx.id}><span>{tx.name}<small>{dateLabel(tx.date)} · {tx.category_label}</small></span><strong className={tx.signed_amount > 0 ? 'inflow' : ''}>{money(tx.signed_amount)}</strong></div>)}{!filtered.length && <p>No hay movimientos que coincidan.</p>}
        </motion.section>}
        {page === 'calendar' && <motion.section className="glass page-panel calendar-page" key="calendar-page"
          initial={{ opacity: 0, x: shouldReduceMotion ? 0 : 32 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: shouldReduceMotion ? 0 : 32 }}
          transition={{ duration: shouldReduceMotion ? 0 : 0.45, ease: MOTION_EASE }}>
          <header className="card-heading"><h2>Calendario de gastos</h2>{backToHome}</header>
          <p className="muted-copy">Gastos recurrentes que Kivo anticipa a partir de tu cadencia real de compras -- no son montos inventados.</p>
          {calendarLoading && <p className="empty">Cargando calendario…</p>}
          {calendarError && <div className="error-banner" role="alert">{calendarError} <button onClick={loadCalendar}>Reintentar</button></div>}
          {!calendarLoading && !calendarError && calendarDisplayedYearMonth && <>
            <div className="calendar-nav">
              <button type="button" className="icon-button" aria-label="Mes anterior" disabled={calendarMonthOffset <= -1} onClick={() => { setCalendarMonthOffset(o => o - 1); setSelectedCalendarDay(null); }}><ChevronLeft /></button>
              <h3 className="calendar-month-label">{monthLabel(calendarDisplayedYearMonth)}</h3>
              <button type="button" className="icon-button" aria-label="Mes siguiente" disabled={calendarMonthOffset >= 2} onClick={() => { setCalendarMonthOffset(o => o + 1); setSelectedCalendarDay(null); }}><ChevronRight /></button>
            </div>
            <div className="calendar-grid">
              {WEEKDAY_LABELS.map(w => <div className="calendar-weekday" key={w}>{w}</div>)}
              {calendarCells.map((iso, index) => {
                if (!iso) return <div className="calendar-cell calendar-cell-empty" key={`empty-${index}`} />;
                const dayExpenses = calendarExpensesByDate[iso] || [];
                const isToday = iso === calendarReferenceDate;
                const isSelected = iso === selectedCalendarDay;
                return <button type="button" key={iso} aria-label={`${dateLabel(iso)}${dayExpenses.length ? `, ${money(dayExpenses.reduce((sum, item) => sum + item.expected_amount, 0))} esperados` : ''}`}
                  className={`calendar-cell ${dayExpenses.length ? 'has-expense' : ''} ${isToday ? 'is-today' : ''} ${isSelected ? 'is-selected' : ''}`}
                  onClick={() => dayExpenses.length && setSelectedCalendarDay(isSelected ? null : iso)}>
                  <span className="calendar-day-number">{Number(iso.slice(8))}</span>
                  {dayExpenses.length > 0 && <><span className="calendar-expense-dot" /><span className="calendar-day-amount">{money(dayExpenses.reduce((sum, item) => sum + item.expected_amount, 0))}</span></>}
                </button>;
              })}
            </div>
            {selectedCalendarDay ? <div className="calendar-detail">
              <h3>{dateLabel(selectedCalendarDay)}</h3>
              {selectedCalendarExpenses.map((item, index) => <div className="detail-line" key={index}><span>{item.category_label}<small>{item.days_until === 0 ? 'Hoy' : item.days_until === 1 ? 'Mañana' : item.days_until > 0 ? `En ${item.days_until} días` : `Hace ${-item.days_until} días`} · {item.confidence}% confianza, según tu historial de compras</small></span><strong>{money(item.expected_amount)}</strong></div>)}
            </div> : <p className="empty calendar-hint">{Object.keys(calendarExpensesByDate).length ? 'Toca un día marcado para ver el gasto esperado.' : 'Todavía no hay gastos recurrentes predecibles -- Kivo necesita al menos 3 compras reales de una misma categoría para poder anticiparla.'}</p>}
          </>}
        </motion.section>}
        {page === 'envelopes' && <motion.section className="glass page-panel" key="envelopes-page"
          initial={{ opacity: 0, x: shouldReduceMotion ? 0 : 32 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: shouldReduceMotion ? 0 : 32 }}
          transition={{ duration: shouldReduceMotion ? 0 : 0.45, ease: MOTION_EASE }}>
          <header className="card-heading"><h2>Gastos y metas</h2>{backToHome}</header>
          <p className="muted-copy">Kivo no aparta ni mueve tu dinero. Pon una meta por categoría o una meta de ahorro y te muestro cuánto llevas de verdad -- incluye lo que registraste en efectivo o con otra tarjeta.</p>
          {budgetLoading && <p className="empty">Cargando presupuesto…</p>}
          {budgetError && <div className="error-banner" role="alert">{budgetError}</div>}
          {budgetNotice && <p className="operation-notice" role="status">{budgetNotice}</p>}
          {budgetData && <>
            <h3>Tus metas ({budgetData.budget.categories.length})</h3>
            {budgetData.budget.categories.length ? budgetData.budget.categories.map(cat => <div className="envelope-row" key={cat.category}>
              <div className="envelope-row-top"><span>{cat.label}<small>Meta mensual: {money(cat.monthly_target)}</small></span><span className="envelope-row-actions"><strong>{money(cat.spent)} <span className="muted-copy">de {money(cat.monthly_target)}</span></strong><button type="button" className="text-button envelope-edit-button" onClick={() => { setNewBudgetCategory(cat.category); setNewBudgetTarget(String(cat.monthly_target)); setNewBudgetLabel(cat.label); }}>Editar</button></span></div>
              <div className="envelope-progress"><progress max={cat.monthly_target || 1} value={Math.min(cat.spent, cat.monthly_target || 1)} aria-label={`Progreso de ${cat.label}`} /></div>
              <small className={`budget-pace tone-${cat.pace === 'excedido' ? 'over' : cat.pace === 'apretado' ? 'warn' : 'good'}`}>
                {cat.pace === 'excedido' ? `Ya pasaste tu meta por ${money(cat.spent - cat.monthly_target)}.`
                  : cat.pace === 'apretado' ? 'Vas gastando más rápido que lo que lleva el mes -- ojo.'
                  : 'Vas a buen ritmo.'}
              </small>
            </div>) : <p className="empty">Todavía no tienes ninguna meta -- empieza por donde más se te vaya.</p>}
            {budgetData.budget.unbudgeted.length > 0 && <div className="detail-line"><span>Sin meta<small>{budgetData.budget.unbudgeted.map(u => u.label).join(', ')}</small></span><strong>{money(budgetData.budget.unbudgeted_spend)}</strong></div>}
            {budgetData.monthly_budget != null && budgetData.budget.total_targets > 0 && <p className="muted-copy">
              Tus metas suman {money(budgetData.budget.total_targets)}
              {budgetData.budget.total_targets > budgetData.monthly_budget
                ? ` -- más que tu meta global de ${money(budgetData.monthly_budget)}. Una de las dos tiene que ceder.`
                : ` de tu meta global de ${money(budgetData.monthly_budget)}.`}
            </p>}
            <form className="envelope-form" onSubmit={submitCategoryBudget}>
              <label className="ledger-filter"><span>Categoría</span>
                <select required value={newBudgetCategory} onChange={event => {
                  const category = event.target.value;
                  setNewBudgetCategory(category);
                  const existing = budgetData.budget.categories.find(cat => cat.category === category);
                  setNewBudgetTarget(existing ? String(existing.monthly_target) : '');
                  setNewBudgetLabel(existing ? existing.label : '');
                }}>
                  <option value="">Elige una categoría</option>
                  {BUDGET_CATEGORY_OPTIONS.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
                </select>
              </label>
              <label className="ledger-filter"><span>Nombre (opcional)</span><input type="text" value={newBudgetLabel} onChange={event => setNewBudgetLabel(event.target.value)} placeholder="Ej. Gasolina y camión" /></label>
              <label className="ledger-filter"><span>Meta mensual</span><input type="number" min="0" step="0.01" required value={newBudgetTarget} onChange={event => setNewBudgetTarget(event.target.value)} /></label>
              <button className="black-button small" type="submit" disabled={budgetBusy}>{editingBudgetCategory ? 'Actualizar meta' : 'Crear meta'}</button>
              {editingBudgetCategory && <button type="button" className="text-button" onClick={() => { setNewBudgetCategory(''); setNewBudgetTarget(''); setNewBudgetLabel(''); }}>Cancelar edición</button>}
            </form>
            <h3>Metas de ahorro ({budgetData.budget.goals.length})</h3>
            <p className="muted-copy">A diferencia de las metas de categoría, estas acumulan un total a lo largo de varios meses (ej. un viaje). Créalas hablando con Kivo en el chat -- dile cuánto quieres juntar y para qué, y te dice en cuánto tiempo es realista según tu dinero libre real. <button type="button" className="text-button" onClick={() => setPage('chat')}>Ir al chat</button></p>
            {budgetData.budget.goals.length ? budgetData.budget.goals.map(goal => <div className="envelope-row" key={goal.slug}>
              <div className="envelope-row-top"><span>{goal.label}<small>Meta total: {money(goal.target_amount)} · ~{money(goal.monthly_contribution)}/mes · {goal.estimated_months} meses</small></span><strong>{money(goal.contributed)} <span className="muted-copy">de {money(goal.target_amount)}</span></strong></div>
              <div className="envelope-progress"><progress max={goal.target_amount || 1} value={Math.min(goal.contributed, goal.target_amount || 1)} aria-label={`Progreso de ${goal.label}`} /></div>
              <small className={`budget-pace tone-${goal.pace === 'atrasada' ? 'warn' : 'good'}`}>
                {goal.pace === 'cumplida' ? 'Meta cumplida.'
                  : goal.pace === 'nueva' ? 'Meta recién creada -- registra tu primer aporte cuando apartes dinero por tu cuenta.'
                  : goal.pace === 'bien' ? 'Vas a buen ritmo.'
                  : `Ibas a llevar ~${money(goal.expected_by_now)} para esta fecha -- vas atrás.`}
              </small>
              {!goal.realistic && <small className="budget-pace tone-warn">A tu ritmo libre actual, esta meta tomaría ~{goal.estimated_months} meses (~{Math.round(goal.estimated_months / 12 * 10) / 10} años) -- no es un plazo realista. Considera bajar el monto o pedirle a Kivo un reparto inteligente para ver cuánto margen tienes de verdad.</small>}
            </div>) : <p className="empty">Todavía no tienes metas de ahorro -- pídele a Kivo en el chat que te arme una.</p>}
            {budgetData.budget.goals.length > 0 && <form className="envelope-form" onSubmit={submitGoalContribution}>
              <label className="ledger-filter"><span>Meta</span>
                <select required value={contributionGoal} onChange={event => setContributionGoal(event.target.value)}>
                  <option value="">Elige una meta</option>
                  {budgetData.budget.goals.map(goal => <option key={goal.slug} value={goal.slug}>{goal.label}</option>)}
                </select>
              </label>
              <label className="ledger-filter"><span>Ya aparté</span><input type="number" min="0.01" step="0.01" required value={contributionAmount} onChange={event => setContributionAmount(event.target.value)} placeholder="Ej. 500" /></label>
              <button className="black-button small" type="submit" disabled={budgetBusy}>Registrar aporte</button>
            </form>}
            <h3>Reparto inteligente</h3>
            <p className="muted-copy">Con tu saldo actual, reparte lo que falta de tus metas de categoría y de ahorro -- sin mover nada, y sin tocar tu colchón mínimo de seguridad.</p>
            <button className="outline-button small" onClick={loadSmartAllocation} disabled={smartAllocationLoading}>{smartAllocationLoading ? 'Calculando…' : 'Calcular reparto inteligente'}</button>
            {smartAllocationError && <div className="error-banner" role="alert">{smartAllocationError}</div>}
            {smartAllocation && (smartAllocation.ok ? <>
              <div className="detail-line"><span>Saldo actual</span><strong>{money(smartAllocation.current_balance)}</strong></div>
              <div className="detail-line"><span>Colchón mínimo protegido<small>No se toca, sin importar lo demás</small></span><strong>{money(smartAllocation.cushion_floor)}</strong></div>
              <div className="detail-line"><span>Disponible para repartir</span><strong>{money(smartAllocation.available)}</strong></div>
              {smartAllocation.scaled && <p className="empty">Lo que piden tus metas no cabe en lo disponible -- se escaló todo proporcionalmente.</p>}
              {smartAllocation.lines.map(line => <div className="detail-line" key={line.key}><span>{line.label}<small>{line.kind === 'goal' ? 'Meta de ahorro' : 'Meta de categoría'} · pendiente {money(line.needed)}</small></span><strong>{money(line.suggested)}</strong></div>)}
              <div className="detail-line"><span>Total sugerido</span><strong>{money(smartAllocation.reserved)}</strong></div>
              <div className="detail-line"><span>Te quedaría libre</span><strong>{money(smartAllocation.free)}</strong></div>
            </> : <p className="empty">{smartAllocation.reason}</p>)}
          </>}
        </motion.section>}
        {page === 'benefits' && <motion.section className="glass page-panel benefits-page" key="benefits-page"
          initial={{ opacity: 0, x: shouldReduceMotion ? 0 : 32 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: shouldReduceMotion ? 0 : 32 }}
          transition={{ duration: shouldReduceMotion ? 0 : 0.45, ease: MOTION_EASE }}>
          <header className="card-heading"><h2>Beneficios de tu tarjeta</h2>{backToHome}</header>
          <p className="muted-copy">Lo que ya trae tu tarjeta-credencial, sin que tengas que buscarlo en letras chiquitas.</p>
          <div className="benefits-grid">
            {BENEFITS.map(benefit => <div className="benefit-card" key={benefit.title}>
              <img className="benefit-photo" src={benefit.photo} alt="" loading="lazy" onError={event => { event.target.style.display = 'none'; }} />
              <strong>{benefit.title}</strong>
              <p>{benefit.detail}</p>
            </div>)}
          </div>
        </motion.section>}
        {page === 'home' && <motion.section className="balance-card glass" key="balance-card" variants={dashboardItem} whileHover={hoverLift} transition={{ duration: 0.2 }}><div className="balance-top"><div><h2>Score de resiliencia financiera</h2><div className="total score-hero">{signals?.score.value ?? '—'}<span>/100</span></div>{signals && <TrendBadge trend={signals.score.trend} />}<p className={`score-status-message ${signals ? `is-${scoreStatusFeedback(signals).tone}` : ''}`}>{signals ? scoreStatusFeedback(signals).text : 'Cargando tu score…'}</p></div></div><div className="balance-bottom"><div className="account-orbs"><div className="orb-bridge" /><button className="orb" onClick={() => setPage('transactions')}><strong>{money(data?.balance)}</strong><span>Saldo disponible</span></button><button className="orb purple" onClick={() => setModal('score')}><strong>{signals ? `${signals.liquidity.days_covered} días` : '—'}</strong><span>Gastos cubiertos</span></button><button className="orb" onClick={() => setPage('transactions')}><strong>{money(data?.summary.total_income)}</strong><span>Ingresos registrados</span></button></div></div><button className="outline-button score-details-button" onClick={() => setModal('score')}>Entender mi score</button></motion.section>}
        {page === 'home' && <motion.section className="glass budget-goal-card" key="budget-goal-card" variants={dashboardItem} whileHover={hoverLift} transition={{ duration: 0.2 }}>
          <header className="card-heading"><h2>Meta de gasto del mes</h2>{monthlyBudget != null && budgetSource === 'local' && !editingBudget && <button className="text-button budget-edit-link" onClick={() => { setBudgetInput(String(monthlyBudget)); setEditingBudget(true); }}>Editar</button>}</header>
          {monthlyBudget == null || editingBudget ? <form className="budget-goal-form" onSubmit={submitBudget}>
            <label className="ledger-filter"><span>¿Cuánto quieres gastar como máximo este mes?</span><input type="number" min="1" step="1" required autoFocus value={budgetInput} onChange={event => setBudgetInput(event.target.value)} placeholder="Ej. 7000" /></label>
            <div className="budget-goal-form-actions"><button className="black-button small" type="submit">Guardar meta</button>{monthlyBudget != null && <button type="button" className="text-button" onClick={() => setEditingBudget(false)}>Cancelar</button>}</div>
          </form> : !currentMonthKey ? <p className="empty">Aún no hay movimientos este mes para comparar contra tu meta.</p> : <>
            <p className="muted-copy budget-goal-month">{monthLabel(currentMonthKey)}</p>
            <div className="budget-track"><div className={`budget-fill tone-${budgetTone(budgetPercent)}`} style={{ width: `${Math.min(budgetPercent, 100)}%` }} /></div>
            <div className="budget-goal-numbers"><strong>{money(currentMonthExpense)}</strong><span> de {money(monthlyBudget)} gastados · {Math.round(budgetPercent)}%</span></div>
            <p className={`budget-goal-message tone-${budgetTone(budgetPercent)}`}>{budgetMessage(budgetTone(budgetPercent), budgetPercent, currentMonthExpense - monthlyBudget)}</p>
            {budgetSource === 'backend' && <p className="muted-copy budget-goal-chat-hint">¿Quieres cambiarla? Pídeselo a Kivo en el chat, ej. "cambia mi meta a 5000". <button type="button" className="text-button" onClick={() => setPage('chat')}>Ir al chat</button></p>}
          </>}
        </motion.section>}
        {page === 'home' && <motion.section className="payments-card glass alerts-card" key="alerts-card" variants={dashboardItem} whileHover={hoverLift} transition={{ duration: 0.2 }}><header className="card-heading"><h2>Lo que necesita tu atención</h2><span className="pill">{signals?.alerts.length ?? '—'} alertas</span></header>{signals ? signals.alerts.length ? signals.alerts.map(alert => <article className="live-alert" key={alert.id}><ShieldAlert size={24} /><div><strong>{alert.title}</strong><p>{alert.detail}</p>{alert.annual_cost > 0 && <small>{money(alert.monthly_amount)}/mes · {money(alert.annual_cost)} al año · potencial, no ahorro realizado</small>}</div><button className="black-button small" onClick={() => { setChatInput(buildAlertPrompt(alert)); setPage('chat'); }}>Revisar</button></article>) : <p className="empty">Todo al día: el backend no reporta alertas activas.</p> : <p className="empty">{loading ? 'Consultando alertas…' : 'Alertas no disponibles.'}</p>}<p className="muted-copy">Detener un cargo aquí no lo cancela con el banco ni con el comercio -- Kivo solo deja de contarlo en tu score y tus recordatorios.</p></motion.section>}
      </AnimatePresence>
    </section>
    {page === 'home' && <section className="right-column"><motion.section className="transactions" variants={dashboardItem} whileHover={hoverLift} transition={{ duration: 0.2 }}><header className="transactions-heading"><div><h2>Movimientos</h2><p>Historial de Ana</p></div><button className="black-button small" onClick={() => setPage('transactions')}>Ver todos</button></header><div className="transaction-list">{transactions.slice(-3).reverse().map(tx => <div className="transaction-row live-transaction" key={tx.id}><span className={`direction ${tx.signed_amount > 0 ? 'inflow' : 'outflow'}`}>{tx.signed_amount > 0 ? <ArrowDownLeft size={15} /> : <ArrowUpRight size={15} />}</span><strong title={tx.name}>{tx.name}</strong><span className="transaction-date">{dateLabel(tx.date)}</span><span className={`transaction-amount ${tx.signed_amount > 0 ? 'inflow' : ''}`}>{money(tx.signed_amount)}</span></div>)}{!transactions.length && <p className="empty">{loading ? 'Cargando movimientos…' : data ? 'No hay movimientos registrados.' : 'Historial no disponible.'}</p>}</div></motion.section><CategorySpending summary={data?.summary} loading={loading} variants={dashboardItem} />
      {signals?.upcoming_expenses?.length > 0 && <motion.section className="glass upcoming-preview-card" variants={dashboardItem} whileHover={hoverLift} transition={{ duration: 0.2 }}>
        <header className="card-heading"><h2>Próximos gastos</h2><button className="black-button small" onClick={() => setPage('calendar')}>Ver calendario</button></header>
        <div className="upcoming-preview-list">
          {signals.upcoming_expenses.slice(0, 3).map(item => <div className="upcoming-expense-row" key={item.category}><span>{item.category_label}</span><span className="muted-copy">{item.days_until === 0 ? 'Hoy' : item.days_until === 1 ? 'Mañana' : `En ${item.days_until} días`}</span><strong>{money(item.expected_amount)}</strong></div>)}
        </div>
      </motion.section>}
    </section>}
    {modal && <div className="modal-overlay" onClick={() => setModal(null)}><section className="modal glass" role="dialog" aria-modal="true" aria-labelledby="dialog-title" onClick={event => event.stopPropagation()}><button ref={closeRef} className="close-modal icon-button" aria-label="Cerrar" onClick={() => setModal(null)}><X /></button>
      {modal === 'score' && <><h2 id="dialog-title">Tu score, explicado</h2><p>Indicador propio de resiliencia financiera; no es un score de Buró ni garantiza aprobación de crédito.</p>{signals?.score.breakdown.map(item => <div className="score-factor-bar" key={item.key}>
        <div className="score-factor-bar-top"><span>{item.label}<small className="score-factor-technical">Peso: {item.weight}%</small></span><strong>{item.value}/100</strong></div>
        <progress max="100" value={item.value} aria-label={`${item.label}: ${item.value}/100`} />
        <small className="score-factor-explainer">{SCORE_FACTOR_EXPLAINERS[item.key]}</small>
      </div>)}
        <div className="score-progress-section">
          <h3>Tu progreso</h3>
          <p className="muted-copy">Evolución de tu score en los checkpoints recibidos en esta sesión.</p>
          <div className="health-value">{session.history.at(-1)?.value ?? '—'}<small>/100</small></div>
          <LineChart values={session.history.map(point => point.value)} dates={session.history.map(point => point.date)} label="Evolución del score en los checkpoints recibidos" />
        </div>
        {signals?.activation && <div className="bank-signals-section">
          <h3>Actividad con tu tarjeta del banco</h3>
          <div className="bank-signals">
            <div className="trust-stat">
              <strong>{signals.activation.value}/100</strong>
              <span>Activación · {{ activa: 'Activa', en_riesgo: 'En riesgo', dormida: 'Dormida', sin_datos: 'Sin datos' }[signals.activation.status] || signals.activation.status}</span>
            </div>
            <div className="trust-stat">
              <strong>{signals.wallet_share?.value != null ? `${signals.wallet_share.value}%` : '—'}</strong>
              <span>Wallet share · gasto capturado por el banco</span>
            </div>
          </div>
          <p className="muted-copy">{signals.activation.detail}</p>
          {signals.wallet_share?.value != null && <p className="muted-copy">{signals.wallet_share.detail}</p>}
        </div>}
      </>}
      {modal === 'advance' && <><h2 id="dialog-title">Avanzar la simulación</h2><p>El siguiente checkpoint puede observar la cuenta, detectar una fuga, detener el cargo de FitZone Campus, o mover dinero a ahorro en el sandbox Nessie.</p><p>El backend no permite consultar el checkpoint actual. Si el próximo paso es detener FitZone Campus, al continuar confirmas que ya no lo usas y autorizas detener ese cargo de demostración.</p><div className="agent-alert"><ShieldAlert size={20} /><span>Un contrato anual puede generar penalizaciones o cobranza. Bloquear el cargo no cancela la suscripción con el comercio.</span></div><button className="black-button" disabled={busy || uncertain || !data || !!error || session.done} onClick={() => mutate('advance')}>Confirmo y autorizo el siguiente paso</button><button className="text-button" onClick={() => setModal(null)}>Volver sin avanzar</button></>}
      {modal === 'reset' && <><h2 id="dialog-title">Reiniciar demo</h2><p>Solicitará al backend volver al día 0 y reactivar FitZone Campus en el sandbox compartido. También borra tus metas de ahorro y presupuestos por categoría, para que cada ensayo empiece limpio. Borrará el feed y el chat de esta pestaña, pero no revierte los depósitos o retiros anteriores de Nessie.</p><button className="black-button" disabled={busy} onClick={() => mutate('reset')}>Reiniciar simulación</button></>}
      {modal === 'profile' && <>
        <h2 id="dialog-title">Perfil</h2>
        <div className="profile-header"><span className="user-avatar profile-avatar-lg">A</span><div><strong>Ana</strong><p className="muted-copy">Estudiante universitaria · Tarjeta bancaria sin activar · Sin historial en Buró</p></div></div>
        <div className="detail-line"><span>Usuario<small>ID de demo</small></span><strong>ana</strong></div>
        <div className="detail-line"><span>Cuentas bancarias<small>Checking + Ahorros</small></span><strong>2</strong></div>
        <div className="detail-line"><span>Colchón de liquidez<small>Días de gasto esencial cubiertos</small></span><strong>{signals ? `${signals.liquidity.days_covered} días` : '—'}</strong></div>
        <button className="outline-button profile-logout" onClick={() => { try { localStorage.removeItem(AUTH_KEY); } catch { /* Storage is optional. */ } setModal(null); setAuthed(false); }}>Cerrar sesión</button>
        <div className="demo-controls-section">
          <h3>Control de demo (equipo)</h3>
          <p className="muted-copy">Avanza los checkpoints de la simulación sembrada para ensayar el guion de pitch. No forma parte de la experiencia normal de Ana.</p>
          {controls}
          <p className="muted-copy">Esta es la única acción de la demo que sí mueve dinero real -- y no es de Kivo: simula a quien le paga a Ana (una cuenta externa en el sandbox de Nessie). Sirve para ver el plan de nómina armarse solo, sin que nadie lo dispare a mano.</p>
          <button className="black-button small" onClick={simulatePayroll} disabled={payrollBusy}>{payrollBusy ? 'Procesando en Nessie…' : 'Simular nómina de un tercero'}</button>
        </div>
      </>}
    </section></div>}
  </motion.main>
  <Footer />
  <FaqWidget />
  </>;
}
createRoot(document.getElementById('root')).render(<App />);
