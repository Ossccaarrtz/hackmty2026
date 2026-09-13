import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ChartPie, MessageCircle, Wallet, RefreshCw, X, ShieldAlert, ShieldCheck, ArrowUpRight, ArrowDownLeft, ArrowUp, ArrowDown, Minus, Play, RotateCcw, ChevronRight, Search, Bell, Send, User, Lock, Eye, EyeOff, FileText, Copy, Check, Receipt, Download, PiggyBank } from 'lucide-react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { api, sessionKey } from './api.js';
import { appendCheckpoint, appendChatExchange, emptySession, normalizeData } from './data.js';
import './styles.css';
import CategorySpending from './CategorySpending.jsx';

const money = value => Number.isFinite(value) ? new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value) : '—';
const slugify = category => category.trim().toLowerCase().replaceAll(' ', '_'); // debe calzar con agent_actions._slug() del backend
const dateLabel = date => new Intl.DateTimeFormat('es-MX', { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(new Date(`${date}T00:00:00Z`));
const monthLabel = yearMonth => new Intl.DateTimeFormat('es-MX', { month: 'long', year: 'numeric', timeZone: 'UTC' }).format(new Date(`${yearMonth}-01T00:00:00Z`));
const PENDING_TYPES = ['leak_detected', 'anomaly_pause'];

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
  return <div className="chat-bubble assistant typing-indicator" aria-label="Centinel está escribiendo"><span /><span /><span /></div>;
}
function readSession() {
  try { const saved = JSON.parse(sessionStorage.getItem(sessionKey)); if (Array.isArray(saved?.feed) && Array.isArray(saved?.history)) return { ...emptySession(), ...saved }; } catch { /* Storage is optional. */ }
  return emptySession();
}
function LineChart({ values, label }) {
  if (values.length < 2) return <p className="chart-empty">Se necesitan al menos dos registros para mostrar la evolución.</p>;
  const min = Math.min(...values), range = Math.max(...values) - min || 1;
  const points = values.map((value, i) => `${10 + i * 380 / (values.length - 1)},${150 - (value - min) * 130 / range}`).join(' ');
  return <svg className="real-chart" viewBox="0 0 400 170" role="img" aria-label={label}><polyline points={points} fill="none" stroke="currentColor" strokeWidth="3" strokeLinejoin="round" /><text x="10" y="168">{min.toFixed(0)}</text><text x="355" y="16">{Math.max(...values).toFixed(0)}</text></svg>;
}
function TrendBadge({ trend }) {
  if (trend === 'up') return <span className="trend-badge trend-up"><ArrowUp size={14} /> Subiendo</span>;
  if (trend === 'down') return <span className="trend-badge trend-down"><ArrowDown size={14} /> Bajando</span>;
  return <span className="trend-badge trend-flat"><Minus size={14} /> Estable</span>;
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
const AUTH_KEY = 'centinel:authed';
function readAuthed() {
  try {
    if (localStorage.getItem(AUTH_KEY) === 'true') return true;
    return sessionStorage.getItem(AUTH_KEY) === 'true';
  } catch { return false; }
}
const FAQS = [
  { q: '¿Qué es el Cash-Flow Resilience Score?', a: 'Un indicador propio (0-100) de qué tan resiliente es tu flujo de efectivo, calculado a partir de tu comportamiento real: regularidad de ingreso, ratio esencial/discrecional, recurrencia de bills y colchón de liquidez. No es un score de Buró y no requiere historial crediticio previo.' },
  { q: '¿Cómo detecta Centinel una fuga de dinero?', a: 'Compara tus bills recurrentes contra actividad relacionada real (por ejemplo, un cargo de gimnasio sin visitas asociadas). Si no encuentra esa actividad por un periodo prolongado, la marca como posible fuga y te avisa antes de hacer nada.' },
  { q: '¿Centinel puede mover mi dinero sin avisarme?', a: 'Solo en un caso: mover dinero a tu propio ahorro, porque es reversible y no involucra a terceros. Detener un cargo recurrente siempre pide tu confirmación explícita primero, y te advierte si podría tener implicaciones contractuales.' },
  { q: '¿Qué pasa con mis datos financieros?', a: 'El modelo de lenguaje nunca ve tu historial crudo de transacciones -- solo señales ya derivadas (ej. "score=64, fuga detectada: Gym Co"). Toda la demo corre sobre el sandbox de Capital One Nessie, no datos reales.' },
  { q: '¿Qué significa "Sin anomalías"?', a: 'Centinel compara tu actividad reciente contra tu propio historial. Si detecta un patrón fuera de lo normal, pausa cualquier acción autónoma y te pide confirmar antes de continuar, en vez de actuar solo.' },
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
      {open ? <X size={22} /> : <img src="/capital-one-logo.svg" alt="" />}
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
    <p>Centinel One es un proyecto para el Hackathon de Capital One (Track 1) — no procesa datos financieros reales; todos los movimientos vienen del sandbox de Capital One Nessie.</p>
    <p>El modelo de lenguaje (Gemini) nunca recibe tu historial crudo de transacciones, solo señales ya derivadas por nuestro motor (por ejemplo, "score=64, fuga detectada: Gym Co"). Toda acción que mueve dinero pasa primero por una verificación que confirma que coincide con lo que el motor de señales ya calculó, antes de escribir en Nessie.</p>
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
      <span>© 2026 Centinel One — Capital One Hackathon</span>
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
  const [email, setEmail] = useState('mia@centinelone.com');
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
        <img src="/capital-one-logo.svg" alt="Capital One" className="login-logo" />
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
        <p className="login-footnote">Acceso de demostración — cuenta de Mia precargada.</p>
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
  const [trustReport, setTrustReport] = useState(null);
  const [trustLoading, setTrustLoading] = useState(false);
  const [trustError, setTrustError] = useState('');
  const [summaryCopied, setSummaryCopied] = useState(false);
  const [statementMonth, setStatementMonth] = useState('');
  const [envelopes, setEnvelopes] = useState(null);
  const [envelopesLoading, setEnvelopesLoading] = useState(false);
  const [envelopesError, setEnvelopesError] = useState('');
  const [envelopesNotice, setEnvelopesNotice] = useState('');
  const [envelopesBusy, setEnvelopesBusy] = useState(false);
  const [newEnvelopeCategory, setNewEnvelopeCategory] = useState('');
  const [newEnvelopeTarget, setNewEnvelopeTarget] = useState('');
  const [incomeAmount, setIncomeAmount] = useState('');
  const [incomeFrequency, setIncomeFrequency] = useState('');
  const [payrollBusy, setPayrollBusy] = useState(false);
  const [chatInput, setChatInput] = useState('');
  const locked = useRef(false), requestId = useRef(0), closeRef = useRef(null);
  const signals = data?.signals;
  const transactions = data?.transactions || [];
  const notifications = data?.notifications || [];
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
  const statementExpense = statementTransactions.filter(tx => tx.signed_amount < 0).reduce((sum, tx) => sum - tx.signed_amount, 0);
  const statementBreakdown = Object.entries(
    statementTransactions.filter(tx => tx.signed_amount < 0 && !['savings_transfer'].includes(tx.category) && !tx.category?.startsWith('envelope:'))
      .reduce((acc, tx) => { acc[tx.category_label] = (acc[tx.category_label] || 0) + Math.abs(tx.signed_amount); return acc; }, {})
  ).sort((a, b) => b[1] - a[1]);
  const statementOpening = statementTransactions.length ? statementTransactions[0].running_balance - statementTransactions[0].signed_amount : null;
  const statementClosing = statementTransactions.length ? statementTransactions.at(-1).running_balance : null;
  const editingEnvelope = newEnvelopeCategory.trim() && envelopes?.envelopes.some(env => env.slug === slugify(newEnvelopeCategory));
  const MONEY_MOVING_TYPES = ['bill_stopped', 'savings_moved', 'notification']; // mismo criterio que agent_actions.get_trust_report en el backend
  const trustStats = trustReport ? {
    resolvedLeaks: trustReport.verified_actions.filter(a => a.type === 'bill_stopped').length,
    moneyActions: trustReport.verified_actions.filter(a => MONEY_MOVING_TYPES.includes(a.type)).length,
    confirmations: trustReport.verified_actions.filter(a => a.requires_confirmation).length,
  } : null;

  async function loadTrustReport() {
    setSummaryCopied(false);
    setTrustLoading(true);
    setTrustError('');
    try { setTrustReport(await api.getTrustReport()); }
    catch (err) { setTrustError(err.message); }
    finally { setTrustLoading(false); }
  }
  function copyTrustSummary() {
    if (!trustReport?.summary) return;
    navigator.clipboard?.writeText(trustReport.summary).then(() => {
      setSummaryCopied(true);
      window.setTimeout(() => setSummaryCopied(false), 2000);
    }).catch(() => { /* clipboard is best-effort */ });
  }
  async function loadEnvelopes() {
    setEnvelopesLoading(true);
    setEnvelopesError('');
    try { setEnvelopes(await api.getEnvelopes()); }
    catch (err) { setEnvelopesError(err.message); }
    finally { setEnvelopesLoading(false); }
  }
  async function submitNewEnvelope(event) {
    event.preventDefault();
    if (envelopesBusy) return;
    setEnvelopesBusy(true);
    setEnvelopesNotice('');
    setEnvelopesError('');
    try {
      const result = await api.createEnvelope(newEnvelopeCategory, newEnvelopeTarget);
      setEnvelopesNotice(result.message || 'Apartado guardado.');
      setNewEnvelopeCategory(''); setNewEnvelopeTarget('');
      await loadEnvelopes();
    } catch (err) { setEnvelopesError(err.message); }
    finally { setEnvelopesBusy(false); }
  }
  async function submitIncomePattern(event) {
    event.preventDefault();
    if (envelopesBusy) return;
    setEnvelopesBusy(true);
    setEnvelopesNotice('');
    setEnvelopesError('');
    try {
      const result = await api.setIncomePattern(incomeAmount, incomeFrequency);
      setEnvelopesNotice(result.message || 'Patrón de nómina guardado.');
      setIncomeAmount(''); setIncomeFrequency('');
      await loadEnvelopes();
    } catch (err) { setEnvelopesError(err.message); }
    finally { setEnvelopesBusy(false); }
  }
  async function confirmAllocation() {
    if (envelopesBusy) return;
    setEnvelopesBusy(true);
    setEnvelopesNotice('');
    setEnvelopesError('');
    try {
      const result = await api.confirmPendingAllocation();
      setEnvelopesNotice(result.message || 'Reparto confirmado.');
      await loadEnvelopes();
    } catch (err) { setEnvelopesError(err.message); }
    finally { setEnvelopesBusy(false); }
  }
  async function simulatePayroll() {
    if (payrollBusy) return;
    setPayrollBusy(true);
    setEnvelopesNotice('');
    setEnvelopesError('');
    try {
      const result = await api.simulateThirdPartyPayroll('Estudio Creativo');
      if (!result.matches_income_pattern) {
        setEnvelopesNotice(`${result.message} No se reparte solo a tus apartados.`);
        await loadEnvelopes();
        return;
      }
      setEnvelopesNotice(`${result.message} Esperando a que el webhook reaccione…`);
      // El reparto no es sincrono con esta respuesta: lo dispara DynamoDB
      // Streams -> lambda_notifier unos segundos despues, no esta llamada
      // HTTP. Sin este sondeo breve, la pantalla se queda mostrando saldos
      // viejos y parece que el boton "no hizo nada".
      let updated = null;
      for (let attempt = 0; attempt < 5; attempt++) {
        await new Promise(resolve => setTimeout(resolve, 1500));
        updated = await api.getEnvelopes();
        if (updated.pending_allocation || updated.envelopes.some(env => env.balance > 0)) break;
      }
      if (updated) setEnvelopes(updated);
      if (updated?.pending_allocation) {
        setEnvelopesNotice(`${result.message} Quedó un reparto de ${money(updated.pending_allocation.total)} pendiente de confirmar -- tu colchón de liquidez quedaría muy bajo. Usa "Confirmar reparto pendiente" abajo.`);
      } else if (updated?.envelopes?.some(env => env.balance > 0)) {
        setEnvelopesNotice(`${result.message} Reparto ejecutado automáticamente -- revisa los saldos abajo.`);
      } else {
        setEnvelopesNotice(`${result.message} (El webhook puede tardar unos segundos más -- si los saldos no cambian, vuelve a abrir esta pestaña.)`);
      }
      await refresh();
    } catch (err) { setEnvelopesError(err.message); }
    finally { setPayrollBusy(false); }
  }
  async function refresh() {
    const id = ++requestId.current;
    setLoading(true);
    try {
      const [s, t, n] = await Promise.all([
        api.getSignals(),
        api.getTransactions(),
        api.getNotifications().catch(() => ({ notifications: [] })), // los avisos son un extra -- si fallan, no tumban el dashboard
      ]);
      const next = normalizeData(s, t);
      if (id !== requestId.current) return;
      setData({ ...next, notifications: Array.isArray(n?.notifications) ? n.notifications : [] });
      setError('');
    } catch (err) { if (id === requestId.current) setError(err.message); }
    finally { if (id === requestId.current) setLoading(false); }
  }
  useEffect(() => { refresh(); return () => { requestId.current++; }; }, []);
  useEffect(() => { if (page === 'trust') loadTrustReport(); }, [page]);
  useEffect(() => { if (page === 'envelopes') { setEnvelopesNotice(''); loadEnvelopes(); } }, [page]);
  useEffect(() => { try { sessionStorage.setItem(sessionKey, JSON.stringify(session)); } catch { /* Storage is optional. */ } }, [session]);
  useEffect(() => { try { sessionStorage.setItem(AUTH_KEY, authed ? 'true' : 'false'); } catch { /* Storage is optional. */ } }, [authed]);
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
    try {
      const result = await api.sendChatMessage(text);
      const next = appendChatExchange(session, text, result);
      setSession(next);
      await refresh(); // cualquier accion real que el chat haya ejecutado ya debe reflejarse en signals/transactions
    } catch (err) {
      setSession(s => ({ ...s, chatLog: [...s.chatLog, { id: `chat-err-${Date.now()}`, role: 'assistant', text: `No pude enviar tu mensaje: ${err.message}` }] }));
    } finally { locked.current = false; setBusy(false); setChatBusy(false); }
  }

  const controls = <div className="agent-controls"><button className="black-button small" disabled={busy || loading || !!error || !data || session.done || uncertain} onClick={() => setModal('advance')}><Play size={15} />{busy ? 'Procesando…' : session.done ? 'Demo completada' : 'Avanzar día'}</button><button className="outline-button small" disabled={busy || loading} onClick={() => setModal('reset')}><RotateCcw size={15} /> Reiniciar</button></div>;
  const feed = <div className="agent-feed">{session.feed.length ? session.feed.map(action => <article className={`agent-feed-item ${['error', 'verification_blocked', 'chat_rejected'].includes(action.type) ? 'action-error' : PENDING_TYPES.includes(action.type) || action.requires_confirmation ? 'action-pending' : ''}`} key={action.id}><span>{action.date && dateLabel(action.date)} · {action.type}</span><p>{action.text}</p></article>) : <p className="empty">Todavía no hay acciones recibidas en esta sesión. Avanza la simulación o escríbele a Centinel para ver sus respuestas.</p>}</div>;
  const chatTranscript = <div className="chat-transcript">{session.chatLog.length ? session.chatLog.map(m => <div className={`chat-bubble ${m.role}`} key={m.id}>{renderChatText(m.text)}</div>) : <p className="empty">Escríbele a Centinel: puede revisar tu score, detener una suscripción marcada como fuga, mover dinero a tu ahorro, o liberar parte de tu ahorro si esta semana te entró poco.</p>}{chatBusy && <TypingIndicator />}</div>;

  if (!authed) return <Login onLogin={remember => { if (remember) { try { localStorage.setItem(AUTH_KEY, 'true'); } catch { /* Storage is optional. */ } } setAuthed(true); }} />;

  const isFullPage = page !== 'home';
  const backToHome = <button className="pill" onClick={() => setPage('home')}>Volver al inicio</button>;

  return <>
  <motion.main className={`dashboard connected-dashboard ${isFullPage ? 'full-page-layout page-focused' : ''}`} variants={dashboardContainer} initial="hidden" animate="visible">
    <motion.aside className="sidebar" aria-label="Navegación principal" variants={dashboardItem}><button className="brand-mark" aria-label="Centinel One inicio" onClick={() => setPage('home')}><img src="/capital-one-logo.svg" alt="Capital One" /></button><nav>{[[ChartPie, 'Inicio', 'home'], [MessageCircle, 'Chat con Centinel', 'chat'], [Wallet, 'Movimientos', 'transactions'], [FileText, 'Reporte de confianza', 'trust'], [Receipt, 'Estado de cuenta', 'statement'], [PiggyBank, 'Apartados', 'envelopes']].map(([Icon, label, destination]) => <button className={`nav-button ${page === destination ? 'active' : ''}`} key={destination} aria-label={label} title={label} onClick={() => setPage(destination)}><Icon size={23} /></button>)}</nav><div className="sidebar-bottom"><button className="nav-button notification" aria-label="Avisos" title="Avisos" onClick={() => setModal('notifications')}><Bell size={21} />{notifications.length > 0 && <i />}</button><button className="mia-avatar" aria-label="Perfil de Mia" onClick={() => setModal('profile')}>M</button></div></motion.aside>
    <section className="main-column">
      <motion.header className="page-header" variants={dashboardItem}><div><h1>Centinel One</h1><p>Hola, Mia. Tu progreso financiero, en un solo lugar.</p></div><button className="pill" disabled={loading || busy} aria-label="Actualizar datos" onClick={refresh}><RefreshCw size={16} /> {loading ? 'Cargando…' : 'Actualizar'}</button></motion.header>
      {error && <div className="error-banner" role="alert">{error} <button disabled={loading || busy} onClick={refresh}>Reintentar lectura</button></div>}
      {operationError && <div className="error-banner" role="alert">{operationError}</div>}
      {notice && <p className="operation-notice" role="status">{notice}</p>}
      <AnimatePresence mode="popLayout" initial={false}>
        {page === 'chat' && <motion.section className="glass chat-panel" key="chat-panel"
          initial={{ opacity: 0, x: shouldReduceMotion ? 0 : 32 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: shouldReduceMotion ? 0 : 32 }}
          transition={{ duration: shouldReduceMotion ? 0 : 0.45, ease: MOTION_EASE }}>
          <header className="card-heading"><h2>Conversación con Centinel</h2><button className="pill" onClick={() => setPage('home')}>Volver al inicio</button></header>
          {chatTranscript}
          <form className="chat-form" onSubmit={sendChat}>
            <input aria-label="Mensaje para Centinel" placeholder="Ej. ¿cómo va mi score? / detén el gimnasio / mueve 20 a mi ahorro" value={chatInput} onChange={event => setChatInput(event.target.value)} disabled={busy} />
            <button className="black-button" type="submit" disabled={busy || !chatInput.trim()} aria-label="Enviar mensaje"><Send size={16} /></button>
          </form>
          {controls}
          {feed}
        </motion.section>}
        {page === 'transactions' && <motion.section className="glass page-panel transactions-page" key="transactions-page"
          initial={{ opacity: 0, x: shouldReduceMotion ? 0 : 32 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: shouldReduceMotion ? 0 : 32 }}
          transition={{ duration: shouldReduceMotion ? 0 : 0.45, ease: MOTION_EASE }}>
          <header className="card-heading"><h2>Todos los movimientos</h2>{backToHome}</header>
          <p>{transactions.length} movimientos · Saldo: {money(data?.balance)}</p>
          <div className="transactions-charts">
            <div><h3 className="chart-block-title">Balance histórico</h3><LineChart values={transactions.map(tx => tx.running_balance)} label="Balance histórico calculado por el backend" /></div>
            <CategorySpending summary={data?.summary} loading={loading} />
          </div>
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
        {page === 'trust' && <motion.section className="glass page-panel" key="trust-page"
          initial={{ opacity: 0, x: shouldReduceMotion ? 0 : 32 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: shouldReduceMotion ? 0 : 32 }}
          transition={{ duration: shouldReduceMotion ? 0 : 0.45, ease: MOTION_EASE }}>
          <header className="card-heading"><h2>Reporte de confianza</h2>{backToHome}</header>
          <p className="muted-copy">Evidencia verificada del comportamiento del agente, no una proyección: cada acción listada aquí ocurrió de verdad.</p>
          {trustLoading && <p className="empty">Generando reporte…</p>}
          {trustError && <div className="error-banner" role="alert">{trustError} <button onClick={loadTrustReport}>Reintentar</button></div>}
          {trustReport && <>
            <div className="trust-stats">
              <div className="trust-stat"><strong>{trustStats.resolvedLeaks}</strong><span>Fugas resueltas</span></div>
              <div className="trust-stat"><strong>{trustStats.moneyActions}</strong><span>Acciones reales sobre el dinero</span></div>
              <div className="trust-stat"><strong>{trustStats.confirmations}</strong><span>Confirmaciones pedidas antes de actuar</span></div>
            </div>
            <div className="detail-line"><span>Score actual<small>Tendencia</small></span><strong><TrendBadge trend={trustReport.score.trend} /> {trustReport.score.value}/100</strong></div>
            <LineChart values={trustReport.score_history.map(point => point.value)} label="Historial completo de score" />
            <div className="detail-line"><span>Colchón de liquidez</span><strong>{trustReport.liquidity.days_covered} días</strong></div>
            {trustReport.projection?.weeks_to_ready != null && <div className="detail-line"><span>Proyección</span><strong>~{trustReport.projection.weeks_to_ready} {trustReport.projection.weeks_to_ready === 1 ? 'checkpoint' : 'checkpoints'} para {trustReport.projection.product}</strong></div>}
            <p className="tip-detail">{trustReport.summary}</p>
            <h3>Acciones verificadas ({trustReport.verified_actions.length})</h3>
            {trustReport.verified_actions.length ? trustReport.verified_actions.map((action, index) => <div className="detail-line" key={index}><span>{action.text}<small>{action.date && dateLabel(action.date)} · {action.type}</small></span></div>) : <p className="empty">Sin acciones verificadas todavía.</p>}
            <button className="outline-button" onClick={copyTrustSummary}>{summaryCopied ? <><Check size={16} /> Copiado</> : <><Copy size={16} /> Copiar resumen</>}</button>
          </>}
        </motion.section>}
        {page === 'statement' && <motion.section className="glass page-panel statement-printable" key="statement-page"
          initial={{ opacity: 0, x: shouldReduceMotion ? 0 : 32 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: shouldReduceMotion ? 0 : 32 }}
          transition={{ duration: shouldReduceMotion ? 0 : 0.45, ease: MOTION_EASE }}>
          <div className="statement-header">
            <div><h2>Estado de cuenta</h2><p className="muted-copy">Libro financiero mensual de Mia: en qué se fue el dinero y de dónde vino.</p></div>
            <div className="statement-header-actions">
              <select aria-label="Mes del estado de cuenta" value={activeStatementMonth} onChange={event => setStatementMonth(event.target.value)}>
                {statementMonths.map(ym => <option key={ym} value={ym}>{monthLabel(ym)}</option>)}
              </select>
              <button className="black-button small" onClick={() => window.print()}><Download size={15} /> Descargar PDF</button>
              {backToHome}
            </div>
          </div>
          {!statementTransactions.length ? <p className="empty">Sin movimientos en {activeStatementMonth ? monthLabel(activeStatementMonth) : 'este periodo'}.</p> : <>
            <div className="statement-summary">
              <div className="statement-summary-item"><span>Saldo inicial</span><strong>{money(statementOpening)}</strong></div>
              <div className="statement-summary-item"><span>Ingresos</span><strong className="inflow">+{money(statementIncome)}</strong></div>
              <div className="statement-summary-item"><span>Gastos</span><strong>-{money(statementExpense)}</strong></div>
              <div className="statement-summary-item"><span>Saldo final</span><strong>{money(statementClosing)}</strong></div>
            </div>
            <h3>En qué se fue el dinero</h3>
            {statementBreakdown.map(([label, amount]) => <div className="detail-line" key={label}><span>{label}</span><strong>{money(amount)}</strong></div>)}
            <h3>Movimientos del mes ({statementTransactions.length})</h3>
            {statementTransactions.map(tx => <div className="detail-line" key={tx.id}><span>{tx.name}<small>{dateLabel(tx.date)} · {tx.category_label}</small></span><strong className={tx.signed_amount > 0 ? 'inflow' : ''}>{money(tx.signed_amount)}</strong></div>)}
          </>}
        </motion.section>}
        {page === 'envelopes' && <motion.section className="glass page-panel" key="envelopes-page"
          initial={{ opacity: 0, x: shouldReduceMotion ? 0 : 32 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: shouldReduceMotion ? 0 : 32 }}
          transition={{ duration: shouldReduceMotion ? 0 : 0.45, ease: MOTION_EASE }}>
          <header className="card-heading"><h2>Apartados</h2>{backToHome}</header>
          <p className="muted-copy">Gastos fijos mensuales con reparto proporcional automático cada vez que llega tu nómina -- gasolina, comida, lo que definas.</p>
          {envelopesLoading && <p className="empty">Cargando apartados…</p>}
          {envelopesError && <div className="error-banner" role="alert">{envelopesError}</div>}
          {envelopesNotice && <p className="operation-notice" role="status">{envelopesNotice}</p>}
          {envelopes && <>
            <h3>Patrón de nómina</h3>
            {envelopes.income_pattern ? <div className="detail-line"><span>Declarado<small>Tolerancia {Math.round(envelopes.income_pattern.tolerance_pct * 100)}%</small></span><strong>{money(envelopes.income_pattern.expected_amount)} cada {envelopes.income_pattern.frequency_days} días</strong></div> : <p className="empty">Sin declarar -- los depósitos no se repartirán a tus apartados hasta que definas esto.</p>}
            <form className="envelope-form" onSubmit={submitIncomePattern}>
              <label className="ledger-filter"><span>Monto esperado</span><input type="number" min="0" step="0.01" required value={incomeAmount} onChange={event => setIncomeAmount(event.target.value)} /></label>
              <label className="ledger-filter"><span>Frecuencia (días)</span><input type="number" min="1" step="1" required value={incomeFrequency} onChange={event => setIncomeFrequency(event.target.value)} /></label>
              <button className="outline-button small" type="submit" disabled={envelopesBusy}>{envelopes.income_pattern ? 'Actualizar patrón' : 'Declarar patrón'}</button>
            </form>
            <h3>Tus apartados ({envelopes.envelopes.length})</h3>
            {envelopes.envelopes.length ? envelopes.envelopes.map(env => <div className="envelope-row" key={env.slug}>
              <div className="envelope-row-top"><span>{env.category}<small>Meta mensual: {money(env.monthly_target)}</small></span><span className="envelope-row-actions"><strong>{money(env.balance)} <span className="muted-copy">de {money(env.monthly_target)}</span></strong><button type="button" className="text-button envelope-edit-button" onClick={() => { setNewEnvelopeCategory(env.category); setNewEnvelopeTarget(String(env.monthly_target)); }}>Editar</button></span></div>
              <div className="envelope-progress"><progress max={env.monthly_target || 1} value={Math.min(env.balance, env.monthly_target || 1)} aria-label={`Progreso de ${env.category}`} /></div>
            </div>) : <p className="empty">Todavía no hay apartados creados.</p>}
            <form className="envelope-form" onSubmit={submitNewEnvelope}>
              <label className="ledger-filter"><span>{editingEnvelope ? 'Categoría (editando)' : 'Categoría nueva'}</span><input type="text" required value={newEnvelopeCategory} onChange={event => setNewEnvelopeCategory(event.target.value)} placeholder="Ej. gasolina" /></label>
              <label className="ledger-filter"><span>Meta mensual</span><input type="number" min="0" step="0.01" required value={newEnvelopeTarget} onChange={event => setNewEnvelopeTarget(event.target.value)} /></label>
              <button className="black-button small" type="submit" disabled={envelopesBusy}>{editingEnvelope ? 'Actualizar apartado' : 'Crear apartado'}</button>
              {editingEnvelope && <button type="button" className="text-button" onClick={() => { setNewEnvelopeCategory(''); setNewEnvelopeTarget(''); }}>Cancelar edición</button>}
            </form>
            {envelopes.pending_allocation
              ? <div className="detail-line pending-allocation"><span>Reparto pendiente de confirmar<small>Colchón de liquidez quedaría muy bajo si se repartiera solo</small></span><strong>{money(envelopes.pending_allocation.total)}</strong></div>
              : <p className="empty">No hay ningún reparto pendiente ahorita.</p>}
            <button className="outline-button" onClick={confirmAllocation} disabled={envelopesBusy || !envelopes.pending_allocation}>Confirmar reparto pendiente</button>
            <p className="muted-copy">Si una nómina detectada dejaba tu colchón muy bajo para repartirse sola, la propuesta queda aquí para tu confirmación explícita.</p>
            <h3>Simular nómina de un tercero</h3>
            <p className="muted-copy">Mueve dinero real desde una cuenta Nessie que no es la nuestra (el "empleador" de Mia) hasta su cuenta -- el reparto a apartados que veas después ocurre solo, disparado por el mismo webhook que reacciona a cualquier depósito real.</p>
            <button className="black-button" onClick={simulatePayroll} disabled={payrollBusy}>{payrollBusy ? 'Procesando en Nessie…' : 'Simular nómina de un tercero'}</button>
          </>}
        </motion.section>}
        {page === 'home' && <motion.section className="balance-card glass" key="balance-card" variants={dashboardItem} whileHover={hoverLift} transition={{ duration: 0.2 }}><div className="balance-top"><div><h2>Score de resiliencia financiera</h2><div className="total score-hero">{signals?.score.value ?? '—'}<span>/100</span></div>{signals && <TrendBadge trend={signals.score.trend} />}<p className="muted-copy">Tu flujo de efectivo cuenta tu historia.</p></div><span className={`pill ${signals?.anomaly?.detected ? 'pill-warning' : ''}`}>{signals ? (signals.anomaly?.detected ? <><ShieldAlert size={14} /> Anomalía detectada</> : <><ShieldCheck size={14} /> Sin anomalías</>) : 'Sin datos'}</span></div><div className="balance-bottom"><div className="account-orbs"><div className="orb-bridge" /><button className="orb" onClick={() => setPage('transactions')}><strong>{money(data?.balance)}</strong><span>Saldo del ledger</span></button><button className="orb purple" onClick={() => setModal('score')}><strong>{signals ? `${signals.liquidity.days_covered} días` : '—'}</strong><span>Gastos cubiertos</span></button><button className="orb" onClick={() => setPage('transactions')}><strong>{money(data?.summary.total_income)}</strong><span>Ingresos registrados</span></button></div></div>{signals?.projection?.weeks_to_ready != null && <p className="projection-note">A este ritmo, listo para {signals.projection.product} en ~{signals.projection.weeks_to_ready} {signals.projection.weeks_to_ready === 1 ? 'checkpoint' : 'checkpoints'}.</p>}</motion.section>}
        {page === 'home' && <div className="stats-grid" key="stats-grid"><motion.section className="expense-card glass breakdown-card" variants={dashboardItem} whileHover={hoverLift} transition={{ duration: 0.2 }}><header className="card-heading"><h2>Qué compone tu score</h2></header>{signals ? signals.score.breakdown.map(item => <div className="score-component" key={item.key} title={item.detail}><div><span>{item.label}</span><strong>{item.value}/100</strong></div><progress max="100" value={item.value} aria-label={item.label} /></div>) : <p className="empty">{loading ? 'Cargando componentes…' : 'Sin datos disponibles.'}</p>}<button className="outline-button score-details-button" onClick={() => setModal('score')}>Entender mi score</button></motion.section><motion.section className="health-card" variants={dashboardItem} whileHover={hoverLift} transition={{ duration: 0.2 }}><header className="card-heading"><h2>Tu progreso</h2><span className="pill">Checkpoints</span></header><div className="health-value">{session.history.at(-1)?.value ?? '—'}<small>/100</small></div><p>Último checkpoint recibido</p><LineChart values={session.history.map(point => point.value)} label="Evolución del score en los checkpoints recibidos" /></motion.section></div>}
        {page === 'home' && <motion.section className="payments-card glass alerts-card" key="alerts-card" variants={dashboardItem} whileHover={hoverLift} transition={{ duration: 0.2 }}><header className="card-heading"><h2>Lo que necesita tu atención</h2><span className="pill">{signals?.alerts.length ?? '—'} alertas</span></header>{signals ? signals.alerts.length ? signals.alerts.map(alert => <article className="live-alert" key={alert.id}><ShieldAlert size={24} /><div><strong>{alert.title}</strong><p>{alert.detail}</p>{alert.annual_cost > 0 && <small>{money(alert.monthly_amount)}/mes · {money(alert.annual_cost)} al año · potencial, no ahorro realizado</small>}</div><button className="black-button small" onClick={() => setPage('chat')}>Revisar</button></article>) : <p className="empty">Todo al día: el backend no reporta alertas activas.</p> : <p className="empty">{loading ? 'Consultando alertas…' : 'Alertas no disponibles.'}</p>}{signals?.upcoming_expenses?.length > 0 && <div className="upcoming-expenses"><h3>Próximos gastos esperados</h3>{signals.upcoming_expenses.map(item => <div className="upcoming-expense-row" key={item.category}><span>{item.category_label}</span><span className="muted-copy">{item.days_until === 0 ? 'Hoy' : item.days_until === 1 ? 'Mañana' : `En ${item.days_until} días`} · {dateLabel(item.expected_date)} · {item.confidence}% confianza</span><strong>{money(item.expected_amount)}</strong></div>)}</div>}<p className="muted-copy">Detener un cargo no cancela el contrato con el comercio.</p></motion.section>}
      </AnimatePresence>
    </section>
    {page === 'home' && <section className="right-column"><motion.section className="transactions" variants={dashboardItem} whileHover={hoverLift} transition={{ duration: 0.2 }}><header className="transactions-heading"><div><h2>Movimientos</h2><p>Historial de Mia</p></div><button className="black-button small" onClick={() => setPage('transactions')}>Ver todos</button></header><div className="transaction-list">{transactions.slice(-3).reverse().map(tx => <div className="transaction-row live-transaction" key={tx.id}><span className={`direction ${tx.signed_amount > 0 ? 'inflow' : 'outflow'}`}>{tx.signed_amount > 0 ? <ArrowDownLeft size={15} /> : <ArrowUpRight size={15} />}</span><strong title={tx.name}>{tx.name}</strong><span className="transaction-date">{dateLabel(tx.date)}</span><span className={`transaction-amount ${tx.signed_amount > 0 ? 'inflow' : ''}`}>{money(tx.signed_amount)}</span></div>)}{!transactions.length && <p className="empty">{loading ? 'Cargando movimientos…' : data ? 'No hay movimientos registrados.' : 'Historial no disponible.'}</p>}</div></motion.section><CategorySpending summary={data?.summary} loading={loading} variants={dashboardItem} /></section>}
    {modal && <div className="modal-overlay" onClick={() => setModal(null)}><section className="modal glass" role="dialog" aria-modal="true" aria-labelledby="dialog-title" onClick={event => event.stopPropagation()}><button ref={closeRef} className="close-modal icon-button" aria-label="Cerrar" onClick={() => setModal(null)}><X /></button>
      {modal === 'score' && <><h2 id="dialog-title">Tu score, explicado</h2><p>Indicador propio de resiliencia financiera; no es un score de Buró ni garantiza aprobación de crédito.</p>{signals?.score.breakdown.map(item => <div className="detail-line" key={item.key}><span>{item.label}<small>{item.detail} · Peso: {item.weight}%</small></span><strong>{item.value}/100</strong></div>)}</>}
      {modal === 'notifications' && <><h2 id="dialog-title">Avisos en tiempo real</h2><p className="muted-copy">Generados automáticamente por un webhook (DynamoDB Streams) cada vez que el agente hace un movimiento real — sin que nadie los pida.</p>{notifications.length ? notifications.map(n => <div className="detail-line" key={n.sk}><span>{n.text}<small>{dateLabel(n.date)}</small></span></div>) : <p>Sin avisos todavía.</p>}</>}
      {modal === 'advance' && <><h2 id="dialog-title">Avanzar la simulación</h2><p>El siguiente checkpoint puede observar la cuenta, detectar una fuga, detener el cargo de Gym Co, o mover dinero a ahorro en el sandbox Nessie.</p><p>El backend no permite consultar el checkpoint actual. Si el próximo paso es detener Gym Co, al continuar confirmas que ya no lo usas y autorizas detener ese cargo de demostración.</p><div className="agent-alert"><ShieldAlert size={20} /><span>Un contrato anual puede generar penalizaciones o cobranza. Bloquear el cargo no cancela la suscripción con el comercio.</span></div><button className="black-button" disabled={busy || uncertain || !data || !!error || session.done} onClick={() => mutate('advance')}>Confirmo y autorizo el siguiente paso</button><button className="text-button" onClick={() => setModal(null)}>Volver sin avanzar</button></>}
      {modal === 'reset' && <><h2 id="dialog-title">Reiniciar demo</h2><p>Solicitará al backend volver al día 0 y reactivar Gym Co en el sandbox compartido. Borrará el feed y el chat de esta pestaña, pero no revierte los depósitos o retiros anteriores de Nessie.</p><button className="black-button" disabled={busy} onClick={() => mutate('reset')}>Reiniciar simulación</button></>}
      {modal === 'profile' && <>
        <h2 id="dialog-title">Perfil</h2>
        <div className="profile-header"><span className="mia-avatar profile-avatar-lg">M</span><div><strong>Mia</strong><p className="muted-copy">Ingreso freelance / gig · Sin historial en Buró</p></div></div>
        <div className="detail-line"><span>Usuario<small>ID de demo</small></span><strong>mia</strong></div>
        <div className="detail-line"><span>Cuentas Nessie<small>Checking + Savings, sandbox</small></span><strong>2</strong></div>
        <div className="detail-line"><span>Colchón de liquidez<small>Días de gasto esencial cubiertos</small></span><strong>{signals ? `${signals.liquidity.days_covered} días` : '—'}</strong></div>
        <button className="outline-button profile-logout" onClick={() => { try { localStorage.removeItem(AUTH_KEY); } catch { /* Storage is optional. */ } setModal(null); setAuthed(false); }}>Cerrar sesión</button>
      </>}
    </section></div>}
  </motion.main>
  <Footer />
  <FaqWidget />
  </>;
}
createRoot(document.getElementById('root')).render(<App />);
