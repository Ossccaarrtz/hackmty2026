import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ChartPie, MessageCircle, Wallet, RefreshCw, X, ShieldAlert, ShieldCheck, ArrowUpRight, ArrowDownLeft, ArrowUp, ArrowDown, Minus, Play, RotateCcw, ChevronRight, Search, Bell, Send, User, Lock, Eye, EyeOff, TrendingUp, Ban, PiggyBank, CircleCheck, BadgeAlert } from 'lucide-react';
import { api, sessionKey } from './api.js';
import { appendCheckpoint, appendChatExchange, emptySession, normalizeData } from './data.js';
import './styles.css';

const money = value => Number.isFinite(value) ? new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value) : '—';
const dateLabel = date => new Intl.DateTimeFormat('es-MX', { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(new Date(`${date}T00:00:00Z`));
const PENDING_TYPES = ['leak_detected', 'anomaly_pause'];
const CATEGORY_LABELS = { rent: 'Renta', groceries: 'Supermercado', transport: 'Transporte', utilities: 'Servicios', discretionary: 'Discrecional', income: 'Ingreso' };
const CATEGORY_COLORS = { rent: '#004977', groceries: '#d03027', transport: '#1c74a6', utilities: '#4f95c4', discretionary: '#8390a4', income: '#5ca2fc' };
const ACTION_META = {
  score_check: { icon: TrendingUp, label: 'Revisión de score' },
  leak_detected: { icon: Search, label: 'Fuga detectada' },
  bill_stopped: { icon: Ban, label: 'Cargo detenido' },
  stop_subscription: { icon: Ban, label: 'Suscripción detenida' },
  savings_moved: { icon: PiggyBank, label: 'Ahorro automático' },
  move_to_savings: { icon: PiggyBank, label: 'Movido a ahorro' },
  release_savings_buffer: { icon: PiggyBank, label: 'Colchón liberado' },
  anomaly_pause: { icon: BadgeAlert, label: 'Anomalía detectada' },
  verification_blocked: { icon: BadgeAlert, label: 'Verificación bloqueada' },
  chat_rejected: { icon: BadgeAlert, label: 'Acción rechazada' },
  error: { icon: BadgeAlert, label: 'Error' },
  info: { icon: CircleCheck, label: 'Información' },
};
function actionMeta(type) {
  return ACTION_META[type] || { icon: CircleCheck, label: type };
}
function CategoryPie({ data, size = 128 }) {
  const total = data.reduce((sum, d) => sum + d.value, 0);
  if (!total) return <p className="empty">Sin gastos por categoría todavía.</p>;
  const stroke = size * 0.26;
  const r = (size - stroke) / 2;
  const circumference = 2 * Math.PI * r;
  let offset = 0;
  const slices = data.map(d => {
    const pct = d.value / total;
    const dash = pct * circumference;
    const slice = { ...d, pct: pct * 100, dash, dashOffset: -offset };
    offset += dash;
    return slice;
  });
  return <div className="category-chart-row">
    <div className="category-donut" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label="Gasto por categoría">
        <g transform={`rotate(-90 ${size / 2} ${size / 2})`}>
          <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#e3e9f2" strokeWidth={stroke} />
          {slices.map(s => <circle key={s.label} cx={size / 2} cy={size / 2} r={r} fill="none" stroke={s.color} strokeWidth={stroke} strokeDasharray={`${s.dash} ${circumference - s.dash}`} strokeDashoffset={s.dashOffset} />)}
        </g>
      </svg>
      <div className="category-donut-center"><strong>{money(total)}</strong><span>Total</span></div>
    </div>
    <ul className="category-legend">
      {slices.map(s => <li key={s.label}><span className="category-swatch" style={{ background: s.color }} />{s.label}<strong>{Math.round(s.pct)}%</strong><small>{money(s.value)}</small></li>)}
    </ul>
  </div>;
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
const AUTH_KEY = 'centinel:authed';
function readAuthed() {
  try {
    if (localStorage.getItem(AUTH_KEY) === 'true') return true;
    return sessionStorage.getItem(AUTH_KEY) === 'true';
  } catch { return false; }
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
  function submit(event) { event.preventDefault(); onLogin(remember); }
  return <div className="login-page">
    <header className="login-topbar">
      <img src="/capital-one-logo.svg" alt="Capital One" className="login-topbar-logo" />
      <span className="login-topbar-brand">Centinel One</span>
    </header>
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
    <Footer />
  </div>;
}

function App() {
  const [authed, setAuthed] = useState(readAuthed);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [operationError, setOperationError] = useState('');
  const [uncertain, setUncertain] = useState(false);
  const [session, setSession] = useState(readSession);
  const [page, setPage] = useState('home');
  const [modal, setModal] = useState(null);
  const [query, setQuery] = useState('');
  const [notice, setNotice] = useState('');
  const [chatInput, setChatInput] = useState('');
  const locked = useRef(false), requestId = useRef(0), closeRef = useRef(null);
  const signals = data?.signals;
  const transactions = data?.transactions || [];
  const notifications = data?.notifications || [];
  const filtered = transactions.slice().reverse().filter(tx => `${tx.name} ${tx.category_label}`.toLowerCase().includes(query.toLowerCase()));
  const categoryData = Object.entries(data?.summary?.by_category || {})
    .map(([key, value]) => ({ label: CATEGORY_LABELS[key] || key, value, color: CATEGORY_COLORS[key] || '#8390a4' }))
    .sort((a, b) => b.value - a.value);

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
    locked.current = true; setBusy(true); setOperationError('');
    setChatInput('');
    try {
      const result = await api.sendChatMessage(text);
      const next = appendChatExchange(session, text, result);
      setSession(next);
      await refresh(); // cualquier accion real que el chat haya ejecutado ya debe reflejarse en signals/transactions
    } catch (err) {
      setSession(s => ({ ...s, chatLog: [...s.chatLog, { id: `chat-err-${Date.now()}`, role: 'assistant', text: `No pude enviar tu mensaje: ${err.message}` }] }));
    } finally { locked.current = false; setBusy(false); }
  }

  const controls = <div className="agent-controls"><button className="black-button small" disabled={busy || loading || !!error || !data || session.done || uncertain} onClick={() => setModal('advance')}><Play size={15} />{busy ? 'Procesando…' : session.done ? 'Demo completada' : 'Avanzar día'}</button><button className="outline-button small" disabled={busy || loading} onClick={() => setModal('reset')}><RotateCcw size={15} /> Reiniciar</button></div>;
  const feed = <div className="agent-feed">{session.feed.length ? session.feed.map(action => { const meta = actionMeta(action.type); const Icon = meta.icon; const pending = PENDING_TYPES.includes(action.type) || action.requires_confirmation; const isError = ['error', 'verification_blocked', 'chat_rejected'].includes(action.type); return <article className={`agent-feed-item ${isError ? 'action-error' : pending ? 'action-pending' : ''}`} key={action.id}><span className="agent-feed-icon"><Icon size={16} /></span><div className="agent-feed-body"><span className="agent-feed-meta">{action.date && dateLabel(action.date)} · {meta.label}{pending && <em className="agent-feed-flag">Requiere confirmación</em>}</span><p>{action.text}</p></div></article>; }) : <p className="empty">Todavía no hay acciones recibidas en esta sesión. Avanza la simulación o escríbele a Centinel para ver sus respuestas.</p>}</div>;
  const chatTranscript = <div className="chat-transcript">{session.chatLog.length ? session.chatLog.map(m => <div className={`chat-bubble ${m.role}`} key={m.id}>{m.text}</div>) : <p className="empty">Escríbele a Centinel: puede revisar tu score, detener una suscripción marcada como fuga, mover dinero a tu ahorro, o liberar parte de tu ahorro si esta semana te entró poco.</p>}</div>;

  if (!authed) return <Login onLogin={remember => { if (remember) { try { localStorage.setItem(AUTH_KEY, 'true'); } catch { /* Storage is optional. */ } } setAuthed(true); }} />;

  return <>
  <main className={`dashboard connected-dashboard ${page === 'chat' ? 'chat-layout' : ''}`}>
    <aside className="sidebar" aria-label="Navegación principal"><button className="brand-mark" aria-label="Centinel One inicio" onClick={() => setPage('home')}><img src="/capital-one-logo.svg" alt="Capital One" /></button><nav>{[[ChartPie, 'Inicio', 'home'], [MessageCircle, 'Chat con Centinel', 'chat'], [Wallet, 'Movimientos', 'transactions']].map(([Icon, label, destination]) => <button className={`nav-button ${page === destination ? 'active' : ''}`} key={destination} aria-label={label} title={label} onClick={() => destination === 'transactions' ? setModal('transactions') : setPage(destination)}><Icon size={23} /></button>)}</nav><div className="sidebar-bottom"><button className="nav-button notification" aria-label="Avisos" title="Avisos" onClick={() => setModal('notifications')}><Bell size={21} />{notifications.length > 0 && <i />}</button><button className="mia-avatar" aria-label="Perfil de Mia" onClick={() => setModal('profile')}>M</button></div></aside>
    <section className="main-column">
      <header className="page-header"><div><h1>Centinel One</h1><p>Hola, Mia. Tu progreso financiero, en un solo lugar.</p></div><button className="pill" disabled={loading || busy} aria-label="Actualizar datos" onClick={refresh}><RefreshCw size={16} /> {loading ? 'Cargando…' : 'Actualizar'}</button></header>
      <div className="connection-banner" role="status">Sandbox Nessie · USD · {loading ? 'Consultando backend…' : error ? 'Sin conexión · datos anteriores si están disponibles' : 'Datos del backend'}{session.label && ` · Último checkpoint: ${session.label}`}</div>
      {error && <div className="error-banner" role="alert">{error} <button disabled={loading || busy} onClick={refresh}>Reintentar lectura</button></div>}
      {operationError && <div className="error-banner" role="alert">{operationError}</div>}
      {notice && <p className="operation-notice" role="status">{notice}</p>}
      {page === 'chat' ? <section className="glass chat-panel">
        <header className="card-heading"><h2>Conversación con Centinel</h2><button className="pill" onClick={() => setPage('home')}>Volver al inicio</button></header>
        {chatTranscript}
        <form className="chat-form" onSubmit={sendChat}>
          <input aria-label="Mensaje para Centinel" placeholder="Ej. ¿cómo va mi score? / detén el gimnasio / mueve 20 a mi ahorro" value={chatInput} onChange={event => setChatInput(event.target.value)} disabled={busy} />
          <button className="black-button" type="submit" disabled={busy || !chatInput.trim()} aria-label="Enviar mensaje"><Send size={16} /></button>
        </form>
        {controls}
        {feed}
      </section> : <>
        <section className="balance-card glass"><div className="balance-top"><div><h2>Score de resiliencia financiera</h2><div className="total score-hero">{signals?.score.value ?? '—'}<span>/100</span></div>{signals && <TrendBadge trend={signals.score.trend} />}<p className="muted-copy">Tu flujo de efectivo cuenta tu historia{signals?._debug?.elapsed_days ? ` · basado en ${signals._debug.elapsed_days} días de actividad real` : ''}.</p></div><span className={`pill ${signals?.anomaly?.detected ? 'pill-warning' : ''}`}>{signals ? (signals.anomaly?.detected ? <><ShieldAlert size={14} /> Anomalía detectada</> : <><ShieldCheck size={14} /> Sin anomalías</>) : 'Sin datos'}</span></div><div className="balance-bottom"><dl className="balance-stats"><div><dt>Saldo</dt><dd>{money(data?.balance)}</dd></div><div><dt>Cobertura</dt><dd>{signals ? `${signals.liquidity.days_covered} días` : '—'}</dd></div><div><dt>Ingreso registrado</dt><dd>{money(data?.summary.total_income)}</dd></div></dl><button className="black-button" onClick={() => setPage('chat')}>Hablar con Centinel</button></div>{signals?.projection?.weeks_to_ready != null && <p className="projection-note">A este ritmo, listo para {signals.projection.product} en ~{signals.projection.weeks_to_ready} {signals.projection.weeks_to_ready === 1 ? 'checkpoint' : 'checkpoints'}.</p>}</section>
        <div className="stats-grid"><section className="expense-card glass breakdown-card"><header className="card-heading"><h2>Qué compone tu score</h2></header>{signals ? signals.score.breakdown.map(item => <div className="score-component" key={item.key}><div><span>{item.label}</span><strong>{item.value}/100</strong></div><progress max="100" value={item.value} aria-label={item.label} />{item.detail && <small className="score-component-detail">{item.detail}</small>}</div>) : <p className="empty">{loading ? 'Cargando componentes…' : 'Sin datos disponibles.'}</p>}</section><section className="health-card"><header className="card-heading"><h2>Tu progreso</h2><span className="pill">Checkpoints</span></header><div className="health-value">{session.history.at(-1)?.value ?? '—'}<small>/100</small></div><p>Último checkpoint recibido</p><LineChart values={session.history.map(point => point.value)} label="Evolución del score en los checkpoints recibidos" /></section></div>
        <section className="payments-card glass alerts-card"><header className="card-heading"><h2>Lo que necesita tu atención</h2><span className="pill">{signals?.alerts.length ?? '—'} alertas</span></header>{signals ? signals.alerts.length ? signals.alerts.map(alert => <article className="live-alert" key={alert.id}><ShieldAlert size={24} /><div><strong>{alert.title}</strong><p>{alert.detail}</p>{alert.annual_cost > 0 && <small>{money(alert.annual_cost)} al año · potencial, no ahorro realizado</small>}</div><button className="black-button small" onClick={() => setPage('chat')}>Revisar</button></article>) : <p className="empty">Todo al día: el backend no reporta alertas activas.</p> : <p className="empty">{loading ? 'Consultando alertas…' : 'Alertas no disponibles.'}</p>}<p className="muted-copy">Detener un cargo no cancela el contrato con el comercio.</p></section>
        <section className="glass action-feed-card"><header className="card-heading"><h2>Feed de acciones del agente</h2>{controls}</header>{feed}<button className="text-button" onClick={() => setPage('chat')}>Abrir conversación <ChevronRight size={16} /></button></section>
      </>}
    </section>
    {page === 'home' && <section className="right-column"><section className="glass transactions-summary"><header className="card-heading"><h2>Movimientos</h2><span className="pill">{transactions.length}</span></header><p className="muted-copy">Último: {transactions.length ? `${transactions.at(-1).name} · ${money(transactions.at(-1).signed_amount)}` : 'sin movimientos'}</p><button className="outline-button" onClick={() => setModal('transactions')}>Ver movimientos</button></section><section className="glass category-card"><header className="card-heading"><h2>Gasto por categoría</h2></header>{data ? <CategoryPie data={categoryData} /> : <p className="empty">{loading ? 'Cargando categorías…' : 'Sin datos disponibles.'}</p>}</section></section>}
    {modal && <div className="modal-overlay" onClick={() => setModal(null)}><section className={`modal glass ${modal === 'transactions' ? 'ledger-modal' : ''}`} role="dialog" aria-modal="true" aria-labelledby="dialog-title" onClick={event => event.stopPropagation()}><button ref={closeRef} className="close-modal icon-button" aria-label="Cerrar" onClick={() => setModal(null)}><X /></button>
      {modal === 'score' && <><h2 id="dialog-title">Tu score, explicado</h2><p>Indicador propio de resiliencia financiera; no es un score de Buró ni garantiza aprobación de crédito.</p>{signals?.score.breakdown.map(item => <div className="detail-line" key={item.key}><span>{item.label}<small>{item.detail} · Peso: {item.weight}%</small></span><strong>{item.value}/100</strong></div>)}</>}
      {modal === 'transactions' && <><h2 id="dialog-title">Todos los movimientos</h2><p>{transactions.length} movimientos · Saldo: {money(data?.balance)}</p><LineChart values={transactions.map(tx => tx.running_balance)} label="Balance histórico calculado por el backend" /><label className="ledger-search"><Search size={18} /><input aria-label="Buscar movimientos" placeholder="Buscar comercio o categoría" value={query} onChange={event => setQuery(event.target.value)} /></label>{filtered.map(tx => <div className="detail-line" key={tx.id}><span>{tx.name}<small>{dateLabel(tx.date)} · {tx.category_label}</small></span><strong className={tx.signed_amount > 0 ? 'inflow' : ''}>{money(tx.signed_amount)}</strong></div>)}{!filtered.length && <p>No hay movimientos que coincidan.</p>}</>}
      {modal === 'notifications' && <><h2 id="dialog-title">Avisos en tiempo real</h2><p className="muted-copy">Generados automáticamente por un webhook (DynamoDB Streams) cada vez que el agente hace un movimiento real — sin que nadie los pida.</p>{notifications.length ? notifications.map(n => <div className="detail-line" key={n.sk}><span>{n.text}<small>{dateLabel(n.date)}</small></span></div>) : <p>Sin avisos todavía.</p>}</>}
      {modal === 'advance' && <><h2 id="dialog-title">Avanzar la simulación</h2><p>El siguiente checkpoint puede observar la cuenta, detectar una fuga, detener el cargo de Gym Co, o mover dinero a ahorro en el sandbox Nessie.</p><p>El backend no permite consultar el checkpoint actual. Si el próximo paso es detener Gym Co, al continuar confirmas que ya no lo usas y autorizas detener ese cargo de demostración.</p><div className="agent-alert"><ShieldAlert size={20} /><span>Un contrato anual puede generar penalizaciones o cobranza. Bloquear el cargo no cancela la suscripción con el comercio.</span></div><button className="black-button" disabled={busy || uncertain || !data || !!error || session.done} onClick={() => mutate('advance')}>Confirmo y autorizo el siguiente paso</button><button className="text-button" onClick={() => setModal(null)}>Volver sin avanzar</button></>}
      {modal === 'reset' && <><h2 id="dialog-title">Reiniciar demo</h2><p>Solicitará al backend volver al día 0 y reactivar Gym Co en el sandbox compartido. Borrará el feed y el chat de esta pestaña, pero no revierte los depósitos o retiros anteriores de Nessie.</p><button className="black-button" disabled={busy} onClick={() => mutate('reset')}>Reiniciar simulación</button></>}
      {modal === 'profile' && <>
        <h2 id="dialog-title">Perfil</h2>
        <div className="profile-header"><span className="mia-avatar profile-avatar-lg">M</span><div><strong>Mia</strong><p className="muted-copy">Ingreso freelance / gig · Sin historial en Buró</p></div></div>
        <div className="detail-line"><span>Usuario<small>ID de demo</small></span><strong>mia</strong></div>
        <div className="detail-line"><span>Cuentas Nessie<small>Checking + Savings, sandbox</small></span><strong>2</strong></div>
        <div className="detail-line"><span>Colchón de liquidez<small>Días de gasto esencial cubiertos</small></span><strong>{signals ? `${signals.liquidity.days_covered} días` : '—'}</strong></div>
        <button className="outline-button" onClick={() => { try { localStorage.removeItem(AUTH_KEY); } catch { /* Storage is optional. */ } setModal(null); setAuthed(false); }}>Cerrar sesión</button>
      </>}
    </section></div>}
  </main>
  <Footer />
  </>;
}
createRoot(document.getElementById('root')).render(<App />);
