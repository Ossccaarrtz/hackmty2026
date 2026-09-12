import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ChartPie, MessageCircle, Wallet, RefreshCw, X, Shield, ShieldAlert, ShieldCheck, ArrowUpRight, ArrowDownLeft, ArrowUp, ArrowDown, Minus, Play, RotateCcw, ChevronRight, Search, Bell, Send } from 'lucide-react';
import { api, sessionKey } from './api.js';
import { appendCheckpoint, appendChatExchange, emptySession, normalizeData } from './data.js';
import './styles.css';

const money = value => Number.isFinite(value) ? new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value) : '—';
const dateLabel = date => new Intl.DateTimeFormat('es-MX', { day: 'numeric', month: 'short', timeZone: 'UTC' }).format(new Date(`${date}T00:00:00Z`));
const PENDING_TYPES = ['leak_detected', 'anomaly_pause'];
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
function readAuthed() { try { return sessionStorage.getItem(AUTH_KEY) === 'true'; } catch { return false; } }
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
  function submit(event) { event.preventDefault(); onLogin(); }
  return <div className="login-screen">
    <form className="login-card" onSubmit={submit}>
      <div className="login-brand"><Shield size={28} /><span>Centinel One</span></div>
      <p className="login-tagline">Agente de autonomía financiera · Track 1, Capital One Hackathon 2026</p>
      <label className="login-field"><span>Correo</span><input type="email" autoComplete="username" value={email} onChange={event => setEmail(event.target.value)} required /></label>
      <label className="login-field"><span>Contraseña</span><input type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} required /></label>
      <button className="login-cta" type="submit">Iniciar sesión</button>
      <p className="login-footnote">Acceso de demostración — cuenta de Mia precargada.</p>
    </form>
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
  const feed = <div className="agent-feed">{session.feed.length ? session.feed.map(action => <article className={`agent-feed-item ${['error', 'verification_blocked', 'chat_rejected'].includes(action.type) ? 'action-error' : PENDING_TYPES.includes(action.type) || action.requires_confirmation ? 'action-pending' : ''}`} key={action.id}><span>{action.date && dateLabel(action.date)} · {action.type}</span><p>{action.text}</p></article>) : <p className="empty">Todavía no hay acciones recibidas en esta sesión. Avanza la simulación o escríbele a Centinel para ver sus respuestas.</p>}</div>;
  const chatTranscript = <div className="chat-transcript">{session.chatLog.length ? session.chatLog.map(m => <div className={`chat-bubble ${m.role}`} key={m.id}>{m.text}</div>) : <p className="empty">Escríbele a Centinel: puede revisar tu score, detener una suscripción marcada como fuga, mover dinero a tu ahorro, o liberar parte de tu ahorro si esta semana te entró poco.</p>}</div>;

  if (!authed) return <Login onLogin={() => setAuthed(true)} />;

  return <>
  <main className={`dashboard connected-dashboard ${page === 'chat' ? 'chat-layout' : ''}`}>
    <aside className="sidebar" aria-label="Navegación principal"><button className="brand-mark" aria-label="Centinel One inicio" onClick={() => setPage('home')}><Shield size={34} /></button><nav>{[[ChartPie, 'Inicio', 'home'], [MessageCircle, 'Chat con Centinel', 'chat'], [Wallet, 'Movimientos', 'transactions']].map(([Icon, label, destination]) => <button className={`nav-button ${page === destination ? 'active' : ''}`} key={destination} aria-label={label} title={label} onClick={() => destination === 'transactions' ? setModal('transactions') : setPage(destination)}><Icon size={23} /></button>)}</nav><div className="sidebar-bottom"><button className="nav-button notification" aria-label="Avisos" title="Avisos" onClick={() => setModal('notifications')}><Bell size={21} />{notifications.length > 0 && <i />}</button><button className="mia-avatar" aria-label="Perfil de Mia" onClick={() => setModal('profile')}>M</button></div></aside>
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
        <section className="balance-card glass"><div className="balance-top"><div><h2>Score de resiliencia financiera</h2><div className="total score-hero">{signals?.score.value ?? '—'}<span>/100</span></div>{signals && <TrendBadge trend={signals.score.trend} />}<p className="muted-copy">Tu flujo de efectivo cuenta tu historia.</p></div><span className={`pill ${signals?.anomaly?.detected ? 'pill-warning' : ''}`}>{signals ? (signals.anomaly?.detected ? <><ShieldAlert size={14} /> Anomalía detectada</> : <><ShieldCheck size={14} /> Sin anomalías</>) : 'Sin datos'}</span></div><div className="balance-bottom"><div className="account-orbs"><div className="orb-bridge" /><button className="orb" onClick={() => setModal('transactions')}><strong>{money(data?.balance)}</strong><span>Saldo del ledger</span></button><button className="orb purple" onClick={() => setModal('score')}><strong>{signals ? `${signals.liquidity.days_covered} días` : '—'}</strong><span>Gastos cubiertos</span></button><button className="orb" onClick={() => setModal('transactions')}><strong>{money(data?.summary.total_income)}</strong><span>Ingresos registrados</span></button></div><div className="money-actions"><button className="outline-button" onClick={() => setModal('score')}>Entender mi score</button><button className="black-button" onClick={() => setPage('chat')}>Hablar con Centinel</button></div></div>{signals?.projection?.weeks_to_ready != null && <p className="projection-note">A este ritmo, listo para {signals.projection.product} en ~{signals.projection.weeks_to_ready} {signals.projection.weeks_to_ready === 1 ? 'checkpoint' : 'checkpoints'}.</p>}</section>
        <div className="stats-grid"><section className="expense-card glass breakdown-card"><header className="card-heading"><h2>Qué compone tu score</h2></header>{signals ? signals.score.breakdown.map(item => <div className="score-component" key={item.key} title={item.detail}><div><span>{item.label}</span><strong>{item.value}/100</strong></div><progress max="100" value={item.value} aria-label={item.label} /></div>) : <p className="empty">{loading ? 'Cargando componentes…' : 'Sin datos disponibles.'}</p>}</section><section className="health-card"><header className="card-heading"><h2>Tu progreso</h2><span className="pill">Checkpoints</span></header><div className="health-value">{session.history.at(-1)?.value ?? '—'}<small>/100</small></div><p>Último checkpoint recibido</p><LineChart values={session.history.map(point => point.value)} label="Evolución del score en los checkpoints recibidos" /></section></div>
        <section className="payments-card glass alerts-card"><header className="card-heading"><h2>Lo que necesita tu atención</h2><span className="pill">{signals?.alerts.length ?? '—'} alertas</span></header>{signals ? signals.alerts.length ? signals.alerts.map(alert => <article className="live-alert" key={alert.id}><ShieldAlert size={24} /><div><strong>{alert.title}</strong><p>{alert.detail}</p>{alert.annual_cost > 0 && <small>{money(alert.annual_cost)} al año · potencial, no ahorro realizado</small>}</div><button className="black-button small" onClick={() => setPage('chat')}>Revisar</button></article>) : <p className="empty">Todo al día: el backend no reporta alertas activas.</p> : <p className="empty">{loading ? 'Consultando alertas…' : 'Alertas no disponibles.'}</p>}<p className="muted-copy">Detener un cargo no cancela el contrato con el comercio.</p></section>
      </>}
    </section>
    {page === 'home' && <section className="right-column"><section className="transactions"><header className="transactions-heading"><div><h2>Movimientos</h2><p>Historial de Mia</p></div><button className="black-button small" onClick={() => setModal('transactions')}>Ver todos</button></header><div className="transaction-list">{transactions.slice(-6).reverse().map(tx => <div className="transaction-row live-transaction" key={tx.id}><span className={`direction ${tx.signed_amount > 0 ? 'inflow' : 'outflow'}`}>{tx.signed_amount > 0 ? <ArrowDownLeft size={15} /> : <ArrowUpRight size={15} />}</span><strong title={tx.name}>{tx.name}</strong><span className="transaction-date">{dateLabel(tx.date)}</span><span className={`transaction-amount ${tx.signed_amount > 0 ? 'inflow' : ''}`}>{money(tx.signed_amount)}</span></div>)}{!transactions.length && <p className="empty">{loading ? 'Cargando movimientos…' : data ? 'No hay movimientos registrados.' : 'Historial no disponible.'}</p>}</div></section><section className="glass notifications-panel"><header className="card-heading"><h2>Avisos en tiempo real</h2><span className="pill">{notifications.length}</span></header>{notifications.length ? notifications.slice(0, 5).map(n => <div className="notification-item" key={n.sk}><span>{dateLabel(n.date)}</span><p>{n.text}</p></div>) : <p className="empty">Sin avisos todavía — se generan solos cuando el agente hace un movimiento real (via webhook, sin que nadie los pida).</p>}</section><section className="glass agent-card"><header className="card-heading"><h2>Actividad de Centinel</h2><span className="pill">Modo demo</span></header>{controls}{feed}<button className="text-button" onClick={() => setPage('chat')}>Abrir conversación <ChevronRight size={16} /></button></section></section>}
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
        <button className="outline-button" onClick={() => { setModal(null); setAuthed(false); }}>Cerrar sesión</button>
      </>}
    </section></div>}
  </main>
  <Footer />
  </>;
}
createRoot(document.getElementById('root')).render(<App />);
