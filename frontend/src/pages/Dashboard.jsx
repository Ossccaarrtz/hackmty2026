import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ChartPie, Settings, Bell, LifeBuoy, MessageCircle, TrendingUp, TrendingDown, Search, PiggyBank, Ban, ShieldAlert, CircleCheck } from 'lucide-react';
import { useCentinel } from '../CentinelContext';

const ACTION_ICON = {
  leak_detected: Search,
  bill_stopped: Ban,
  savings_moved: PiggyBank,
  score_check: TrendingUp,
  verification_blocked: ShieldAlert,
  info: CircleCheck,
};

function ScoreHero({ signals }) {
  const trendUp = signals?.score.trend === 'up';
  const TrendIcon = trendUp ? TrendingUp : TrendingDown;
  const value = signals?.score.value ?? 0;

  return (
    <section className="score-hero glass">
      <div className="score-hero-main">
        <h2>Cash-Flow Resilience Score</h2>
        <div className="score-value-row">
          <span className="score-value-xl">{value}</span>
          <span className="score-max">/ 100</span>
          <span className={`score-trend ${trendUp ? 'up' : 'down'}`}><TrendIcon size={15} /> {trendUp ? 'Improving' : 'Declining'}</span>
        </div>
        <p className="score-caption">
          {trendUp
            ? 'Your financial resilience improved recently thanks to healthier cash flow.'
            : 'Your financial resilience needs attention this period.'}
        </p>
        {signals && <p className="score-sub">Covers {signals.liquidity.days_covered} days of essential spend · Ready for {signals.projection.product} in ~{signals.projection.weeks_to_ready} weeks</p>}
      </div>
      <div className="score-ring" style={{ '--pct': value }}>
        <div className="score-ring-inner">{value}%</div>
      </div>
    </section>
  );
}

function Breakdown({ signals }) {
  if (!signals) return null;
  return (
    <section className="panel glass">
      <header className="card-heading"><h2>Score Breakdown</h2></header>
      <div className="breakdown-list">
        {signals.score.breakdown.map((item) => (
          <div className="breakdown-row" key={item.key}>
            <div className="breakdown-head">
              <span>{item.label}</span>
              <span className="breakdown-value">{item.value}/100 <small>· {item.weight}%</small></span>
            </div>
            <div className="breakdown-track"><div className="breakdown-fill" style={{ width: `${item.value}%` }} /></div>
            {item.detail && <p className="breakdown-detail">{item.detail}</p>}
          </div>
        ))}
      </div>
    </section>
  );
}

function ActionFeed({ actionLog }) {
  return (
    <section className="panel glass action-feed">
      <header className="card-heading"><h2>AI Action Timeline</h2></header>
      {actionLog.length === 0
        ? <p className="empty-note">No actions recorded yet.</p>
        : <div className="timeline">
            {actionLog.map((a) => {
              const Icon = ACTION_ICON[a.type] || CircleCheck;
              return (
                <div className="timeline-item" key={a.id}>
                  <span className="timeline-icon"><Icon size={16} /></span>
                  <div>
                    <p className="timeline-text">{a.text}</p>
                    <p className="timeline-date">{a.date}</p>
                  </div>
                </div>
              );
            })}
          </div>}
    </section>
  );
}

function AlertsCard({ signals, onDismiss }) {
  const navigate = useNavigate();
  const active = (signals?.alerts || []).filter((a) => a.status !== 'resolved');

  return (
    <section className="panel glass">
      <header className="card-heading"><h2>Active Alerts</h2></header>
      {active.length === 0
        ? <p className="empty-note">No active alerts — all clear.</p>
        : active.map((alert) => (
          <div className="alert-card" key={alert.id}>
            <div className="alert-card-head">
              <div>
                <strong>{alert.title}</strong>
                <p className="alert-detail">{alert.detail}</p>
              </div>
              <span className="alert-badge">{alert.severity === 'high' ? 'High priority' : 'Detected'}</span>
            </div>
            <p className="alert-cost">Annual impact: ${alert.annual_cost}</p>
            <div className="alert-actions">
              <button className="black-button small" onClick={() => navigate('/chat')}>Review</button>
              <button className="outline-button small" onClick={() => onDismiss(alert.id)}>Dismiss</button>
            </div>
          </div>
        ))}
    </section>
  );
}

export default function Dashboard() {
  const { signals, transactions, balance, actionLog, loading, liveDataError, dismissAlert } = useCentinel();
  const navigate = useNavigate();
  const [showAllTx, setShowAllTx] = useState(false);
  const activeAlerts = (signals?.alerts || []).filter((a) => a.status !== 'resolved');

  return (
    <main className="dashboard">
      <aside className="sidebar" aria-label="Main navigation">
        <a href="#" className="brand-mark" aria-label="Centinel One home"><svg viewBox="0 0 36 48"><path d="M6 4h15l-7 10H4zM20 15h12l7 10H18zM5 25h11v24L5 41z" fill="currentColor" /></svg></a>
        <nav>
          <button className="nav-button active" aria-label="Overview" title="Overview"><ChartPie size={23} strokeWidth={1.7} /></button>
          <button className="nav-button" aria-label="Settings" title="Settings"><Settings size={23} strokeWidth={1.7} /></button>
        </nav>
        <div className="sidebar-bottom">
          <button className="nav-button notification" aria-label="Alerts"><Bell size={23} />{activeAlerts.length > 0 && <i />}</button>
          <button className="nav-button" aria-label="Help"><LifeBuoy size={23} /></button>
          <button className="profile" aria-label="Profile"><img src="https://i.pravatar.cc/100?img=47" alt="Mia's profile" /></button>
        </div>
      </aside>

      <section className="main-column">
        <header className="page-header">
          <div><h1>Centinel One</h1><p>User: Mia {liveDataError && '· demo data (live backend unreachable)'}</p></div>
        </header>

        {loading ? <p className="empty-note">Loading your live Cash-Flow Resilience Score…</p> : <>
          <ScoreHero signals={signals} />
          <div className="stats-grid two-col">
            <Breakdown signals={signals} />
            <AlertsCard signals={signals} onDismiss={dismissAlert} />
          </div>
          <ActionFeed actionLog={actionLog} />
        </>}
      </section>

      <section className="right-column">
        <section className="transactions glass">
          <header className="transactions-heading">
            <div><h2>Transactions</h2><p>Latest transfers</p></div>
          </header>
          <div className="transaction-list">
            {(showAllTx ? transactions : transactions.slice(0, 6)).map((t, i) => (
              <div className="transaction-row" key={`${t.name}-${i}`}>
                <strong title={t.name}>{t.name}</strong>
                <span className="transaction-date">{t.date}</span>
                <span className="transaction-amount">{t.amount < 0 ? '-' : '+'}${Math.abs(t.amount).toFixed(0)}</span>
              </div>
            ))}
          </div>
          {transactions.length > 6 && (
            <button className="outline-button small view-all" onClick={() => setShowAllTx((v) => !v)}>
              {showAllTx ? 'Show less' : `View all ${transactions.length} transactions`}
            </button>
          )}
        </section>
      </section>

      <button className="chat-fab" aria-label="Open AI Coach" onClick={() => navigate('/chat')}>
        <MessageCircle size={22} />
      </button>
    </main>
  );
}
