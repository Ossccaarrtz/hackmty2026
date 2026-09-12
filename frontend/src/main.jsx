import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { UsersRound, ChartPie, BriefcaseBusiness, CodeXml, Settings, Bell, LifeBuoy, Search, ArrowUpRight, ArrowDownLeft, Plus, ChevronRight, RefreshCw, X, Check, Copy, Figma, LoaderPinwheel } from 'lucide-react';
import './styles.css';

const initialTransactions = [
  { name: 'YouTube', date: 'Jun 15', status: 'Pending', amount: -50 },
  { name: 'John Doe', date: 'Jun 14', status: 'Done', amount: -100 },
  { name: 'Sans Brothers', date: 'Jun 13', status: 'Done', amount: 120 },
  { name: 'John Doe', date: 'Jun 8', status: 'Done', amount: -100 },
  { name: 'Cinema City', date: 'Jun 6', status: 'Done', amount: -75 },
  { name: 'To USD', date: 'Jun 1', status: 'Done', amount: -250 },
];
const initialContacts = [{ name: 'F. Alonso', photo: '12' }, { name: 'C. Leclerc', photo: '13' }, { name: 'M. Naira', photo: '44' }];
const payments = [{ name: 'Stripe Pricing', date: 'Today', plan: 'Payment Links', amount: 1200, icon: 'stripe' }, { name: 'FigJam Membership', date: 'Jun 23', plan: 'Professional', amount: 155, icon: 'figma' }, { name: 'Loom Subscription', date: 'Jul 15', plan: 'Loom Business', amount: 100, icon: 'loom' }];

function App() {
  const [currency, setCurrency] = useState('USD');
  const [period, setPeriod] = useState('Monthly');
  const [modal, setModal] = useState(null);
  const [transactions, setTransactions] = useState(initialTransactions);
  const [contacts, setContacts] = useState(initialContacts);
  const [selected, setSelected] = useState(null);
  const [amount, setAmount] = useState('100.00');
  const [balance, setBalance] = useState(73558);
  const [query, setQuery] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const [toast, setToast] = useState('');
  const [contactTab, setContactTab] = useState('Contacts');
  const symbol = currency === 'USD' ? '$' : '€';
  const money = (value, decimals = 0) => symbol + (value * (currency === 'EUR' ? 0.92 : 1)).toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  const notify = (message) => { setToast(message); window.setTimeout(() => setToast(''), 4000); };
  function send(event) {
    event.preventDefault();
    const value = Number(amount) / (currency === 'EUR' ? 0.92 : 1);
    if (!selected) { setModal('send'); return; }
    if (!Number.isFinite(value) || value <= 0 || value > balance) { notify('Enter a valid amount within your balance.'); return; }
    setBalance(b => b - value);
    setTransactions(t => [{ name: selected.name, date: 'Today', status: 'Pending', amount: -value }, ...t]);
    setModal(null); notify(`Demo transfer of ${money(value, 2)} to ${selected.name} created.`);
  }
  return <main className="dashboard">
    <aside className="sidebar" aria-label="Main navigation">
      <a href="#" className="brand-mark" aria-label="FundFlow home"><svg viewBox="0 0 36 48"><path d="M6 4h15l-7 10H4zM20 15h12l7 10H18zM5 25h11v24L5 41z" fill="currentColor" /></svg></a>
      <nav>{[[UsersRound, 'Contacts'], [ChartPie, 'Overview'], [BriefcaseBusiness, 'Accounts'], [CodeXml, 'Integrations'], [Settings, 'Settings']].map(([Icon, label]) => <button key={label} className={`nav-button ${label === 'Overview' ? 'active' : ''}`} aria-label={label} title={label} onClick={() => label !== 'Overview' && setModal(label.toLowerCase())}><Icon size={23} strokeWidth={1.7} /></button>)}</nav>
      <div className="sidebar-bottom"><button className="nav-button notification" aria-label="Notifications" onClick={() => setModal('notifications')}><Bell size={23} /><i /></button><button className="nav-button" aria-label="Help" onClick={() => setModal('help')}><LifeBuoy size={23} /></button><button className="profile" aria-label="Profile" onClick={() => setModal('profile')}><img src="https://i.pravatar.cc/100?img=11" alt="Your profile" /></button></div>
    </aside>

    <section className="main-column">
      <header className="page-header"><div><h1>FundFlow</h1><p>Start managing your finances</p></div><button className="bank-card" onClick={() => setModal('card')} aria-label="View card ending in 4168"><span>**** 4168</span><span>01/29</span></button></header>
      <section className="balance-card glass">
        <div className="balance-top"><div><h2>Total balance</h2><div className="total"><span>{symbol}</span>{money(balance, 2).slice(1)}</div></div><div className="segmented" aria-label="Currency">{['EUR', 'USD'].map(c => <button key={c} className={currency === c ? 'selected' : ''} onClick={() => setCurrency(c)}>{c}</button>)}</div></div>
        <div className="balance-bottom"><div className="account-orbs"><div className="orb-bridge" /><button className="orb" onClick={() => setModal('visa')}><strong>{money(10208)}</strong><span>Visa</span></button><button className="orb purple" onClick={() => setModal('mastercard')}><strong>{money(23558)}</strong><span>Mastercard</span></button><button className="orb" onClick={() => setModal('savings')}><strong>{money(39792 - (73558 - balance))}</strong><span>Savings</span></button></div><div className="money-actions"><button className="outline-button" onClick={() => setModal('receive')}>Receive Money</button><button className="black-button" onClick={() => setModal('send')}>Send Money</button></div></div>
      </section>

      <div className="stats-grid"><section className="expense-card glass"><header className="card-heading"><h2>Expense statistic</h2><button className="pill" onClick={() => setPeriod(p => p === 'Monthly' ? 'Weekly' : 'Monthly')}>{period}</button></header><div className="bar-chart" aria-label={`${period} expenses chart`}>{(period === 'Monthly' ? [66, 43, 80, 58, 66] : [45, 65, 80, 49, 61]).map((height, i) => <button className={`bar-column ${i === 2 ? 'highlight' : ''}`} key={i} onClick={() => notify(`${period === 'Monthly' ? ['May', 'June', 'July', 'August', 'September'][i] : `Week ${i + 1}`}: ${money(i === 2 ? 45000 : height * 500)} in expenses`)}><div className="bar" style={{ height: `${height}%` }}>{i === 2 && <><i className="chart-dot" /><span className="chart-tooltip">$45k</span></>}</div><span className="bar-label">{period === 'Monthly' ? ['MAY', 'JUN', 'JUL', 'AUG', 'SEP'][i] : ['W1', 'W2', 'W3', 'W4', 'W5'][i]}</span></button>)}</div></section>
      <section className="health-card"><header className="card-heading"><h2>Financial health</h2><button className="refresh" aria-label="Financial health details" onClick={() => setModal('health')}><RefreshCw size={17} /></button></header><div className="health-value">85%</div><p>since last month</p><svg className="line-chart" viewBox="0 0 400 200" preserveAspectRatio="none" aria-label="Financial health increased by 16.75 percent"><defs><linearGradient id="lineFade"><stop stopColor="#e9f7ff" stopOpacity=".15"/><stop offset=".72" stopColor="#f2ffff"/><stop offset="1" stopColor="#d3ecff" stopOpacity=".2"/></linearGradient></defs><path d="M0 195 C25 190 38 176 50 164 S85 106 101 110 S138 167 156 144 S185 78 202 99 S229 147 248 110 S280 35 294 27 S325 -16 347 24 S362 61 376 64" fill="none" stroke="url(#lineFade)" strokeWidth="2.6"/><circle cx="50" cy="164" r="4" fill="#eefeff"/><circle cx="294" cy="27" r="5" stroke="#f1ffff" strokeWidth="3" fill="#8bbdf7"/><text x="7" y="148">726k</text><text x="297" y="58">16.75%</text></svg></section></div>

      <section className="payments-card glass"><header className="card-heading"><h2>Upcoming payments</h2><button className="black-button small" onClick={() => setModal('payments')}>View All</button></header><div className="payment-list">{payments.map(p => <button className="payment-row" key={p.name} onClick={() => setModal(p.name)}><span className="merchant-icon">{p.icon === 'stripe' ? <b>stripe</b> : p.icon === 'figma' ? <Figma size={18} /> : <LoaderPinwheel size={21} />}</span><strong>{p.name}</strong><span className={p.date === 'Today' ? 'status pending' : 'payment-date'}>{p.date}</span><span className="payment-plan">{p.plan}</span><strong className="payment-amount">{money(p.amount)}</strong></button>)}</div></section>
    </section>

    <section className="right-column"><section className="transactions"><header className="transactions-heading"><div><h2>Transactions</h2><p>Latest transfers</p></div><div className="transaction-actions"><button className="icon-button" aria-label="Search transactions" onClick={() => setSearchOpen(v => !v)}><Search size={22} /></button><button className="black-button small" onClick={() => setModal('transactions')}>View All</button></div></header>{searchOpen && <input className="search-input" autoFocus placeholder="Search transactions…" value={query} onChange={e => setQuery(e.target.value)} aria-label="Search transactions" />}<div className="transaction-list">{transactions.filter(t => t.name.toLowerCase().includes(query.toLowerCase())).slice(0, 6).map((t, i) => <div className="transaction-row" key={`${t.name}-${i}`}><span className="direction">{t.amount > 0 ? <ArrowDownLeft size={15} /> : <ArrowUpRight size={15} />}</span><strong title={t.name}>{t.name === 'Sans Brothers' ? 'Sans Broth...' : t.name}</strong><span className="transaction-date">{t.date}</span><span className={`status ${t.status === 'Pending' ? 'pending' : ''}`}>{t.status}</span><span className="transaction-amount">{t.amount < 0 ? '-' : ''}{money(Math.abs(t.amount))}</span></div>)}{query && !transactions.some(t => t.name.toLowerCase().includes(query.toLowerCase())) && <p className="empty">No transactions found.</p>}</div></section>
      <article className="saving-tip"><h3>How to reduce expenses by 25%?</h3><p>View these useful tips to save your money.</p><button onClick={() => setModal('tips')}>Learn more</button></article>
      <section className="quick-transfer glass"><header className="card-heading"><h2>Quick transfer</h2><div className="segmented">{['All', 'Contacts'].map(tab => <button key={tab} className={contactTab === tab ? 'selected' : ''} onClick={() => setContactTab(tab)}>{tab}</button>)}</div></header><div className="contacts"><button className="contact" onClick={() => setModal('add')}><span className="add-avatar"><Plus size={24} strokeWidth={1.5} /></span><span>Add new</span></button>{(contactTab === 'All' ? [...contacts, { name: 'John Doe', photo: '33' }] : contacts).map(c => <button key={c.name} className={`contact ${selected?.name === c.name ? 'chosen' : ''}`} onClick={() => setSelected(c)}><img src={`https://i.pravatar.cc/100?img=${c.photo}`} alt="" /><span>{c.name}</span></button>)}<button className="next-contact" aria-label="See all contacts" onClick={() => setModal('contacts')}><ChevronRight size={20} /></button></div><form className="transfer-form" onSubmit={send}><label><span>{symbol}</span><input aria-label="Transfer amount" type="number" min="0.01" step="0.01" required value={amount} onChange={e => setAmount(e.target.value)} /></label><button className="black-button" type="submit">Send</button></form></section>
    </section>

    {toast && <div className="toast" role="status"><Check size={18} />{toast}</div>}
    {modal && <div className="modal-overlay" onClick={() => setModal(null)}><section className="modal glass" role="dialog" aria-modal="true" aria-label={modal} onClick={e => e.stopPropagation()} onKeyDown={e => e.key === 'Escape' && setModal(null)}><button autoFocus className="close-modal icon-button" aria-label="Close" onClick={() => setModal(null)}><X /></button><ModalContent modal={modal} contacts={contacts} selected={selected} setSelected={setSelected} amount={amount} setAmount={setAmount} send={send} money={money} transactions={transactions} setContacts={setContacts} setModal={setModal} notify={notify} /></section></div>}
  </main>;
}

function ModalContent({ modal, contacts, selected, setSelected, amount, setAmount, send, money, transactions, setContacts, setModal, notify }) {
  if (modal === 'send') return <><h2>Send money</h2><p>Choose a contact and an amount for your demo transfer.</p><form onSubmit={send}><label>Recipient<select required value={selected?.name || ''} onChange={e => setSelected(contacts.find(c => c.name === e.target.value))}><option value="" disabled>Select a contact</option>{contacts.map(c => <option key={c.name}>{c.name}</option>)}</select></label><label>Amount<input type="number" min="0.01" step="0.01" required value={amount} onChange={e => setAmount(e.target.value)} /></label><button className="black-button">Send money</button></form></>;
  if (modal === 'add') return <><h2>Add a contact</h2><p>Save someone for your next transfer.</p><form onSubmit={e => { e.preventDefault(); const name = new FormData(e.currentTarget).get('name').trim(); if (!name) return; setContacts(c => [...c, { name, photo: '33' }]); setModal(null); notify('Contact added.'); }}><label>Full name<input name="name" required maxLength="40" placeholder="e.g. Alex Morgan" /></label><button className="black-button">Add contact</button></form></>;
  if (modal === 'receive') return <><h2>Receive money</h2><p>Use your demo account details to receive a transfer.</p><div className="detail-line"><span>Account</span><strong>FundFlow USD</strong></div><div className="detail-line"><span>Account number</span><strong>0000 4168 0129</strong></div><button className="black-button" onClick={async () => { try { await navigator.clipboard.writeText('0000 4168 0129'); notify('Account number copied.'); } catch { notify('Account number: 0000 4168 0129'); } }}><Copy size={16} /> Copy account number</button></>;
  if (modal === 'transactions') return <><h2>All transactions</h2><p>Your recent account activity.</p>{transactions.map((t, i) => <div className="detail-line" key={i}><span>{t.name}<small>{t.date} · {t.status}</small></span><strong>{t.amount < 0 ? '-' : '+'}{money(Math.abs(t.amount))}</strong></div>)}</>;
  if (modal === 'contacts') return <><h2>Your contacts</h2><p>Select someone for a quick transfer.</p>{contacts.map(c => <button className="detail-line contact-detail" key={c.name} onClick={() => { setSelected(c); setModal('send'); }}><img src={`https://i.pravatar.cc/100?img=${c.photo}`} alt="" /><strong>{c.name}</strong><ArrowUpRight size={18}/></button>)}</>;
  if (modal === 'tips') return <><h2>A little less spending.<br/>A little more freedom.</h2><p>Start with these three simple habits.</p><div className="tip-detail"><b>01 · Review your subscriptions</b><p>Cancel services you no longer use and check for duplicate memberships.</p><b>02 · Set a weekly spending limit</b><p>Give dining, shopping and entertainment their own budget.</p><b>03 · Make saving automatic</b><p>Set aside part of each payment you receive and review your progress monthly.</p></div></>;
  if (modal === 'health') return <><h2>Financial health</h2><div className="modal-score">85%</div><p>Your demo financial health is up 16.75%. Consistent savings and manageable expenses are helping your balance grow.</p></>;
  if (modal === 'payments' || payments.some(p => p.name === modal)) return <><h2>{modal === 'payments' ? 'Upcoming payments' : modal}</h2><p>Your scheduled subscriptions.</p>{payments.filter(p => modal === 'payments' || p.name === modal).map(p => <div className="detail-line" key={p.name}><span>{p.name}<small>{p.date} · {p.plan}</small></span><strong>{money(p.amount)}</strong></div>)}</>;
  const content = { accounts: ['Your accounts', 'Visa · ' + money(10208), 'Mastercard · ' + money(23558), 'Savings · ' + money(39792)], visa: ['Visa', 'Debit account', 'Available balance · ' + money(10208)], mastercard: ['Mastercard', 'Card ending in 4168', 'Available balance · ' + money(23558)], savings: ['Savings', 'Personal savings account', 'All amounts shown are demo data.'], card: ['Your Mastercard', 'Card number · **** **** **** 4168', 'Valid through · 01/29'], integrations: ['Integrations', 'No integrations connected.', 'This dashboard currently uses local demo data.'], settings: ['Settings', 'Display currency can be changed from your total balance card.', 'Your demo session resets when you reload the page.'], notifications: ['Notifications', 'Your Stripe payment of $1,200 is due today.', 'Your financial health increased this month.'], help: ['Here to help', 'Select a contact, enter an amount and press Send to try a demo transfer.', 'Use Receive Money to view your sample account details.'], profile: ['Your profile', 'Alex Morgan', 'Personal account · Demo workspace'] }[modal] || ['FundFlow'];
  return <><h2>{content[0]}</h2>{content.slice(1).map(t => <p key={t}>{t}</p>)}</>;
}

createRoot(document.getElementById('root')).render(<App />);
