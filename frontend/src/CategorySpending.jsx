import React from 'react';
import { motion, useReducedMotion } from 'framer-motion';

const categories = {
  rent: { label: 'Renta', color: '#7469e8' },
  groceries: { label: 'Supermercado', color: '#8da5f1' },
  discretionary: { label: 'Discrecional', color: '#a9b6ca' },
  transport: { label: 'Transporte', color: '#5b98d7' },
  utilities: { label: 'Servicios', color: '#a0cde7' },
};
const money = value => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value);

export default function CategorySpending({ summary, loading, variants }) {
  const reduceMotion = useReducedMotion();
  const entries = Object.entries(summary?.by_category || {})
<<<<<<< HEAD
    .filter(([key, amount]) => !['income', 'savings_transfer'].includes(key) && !key.startsWith('envelope:') && Number.isFinite(amount) && amount > 0)
    .sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((sum, [, amount]) => sum + amount, 0);
  let offset = 0;
  return <motion.section className="glass category-spending" aria-labelledby="category-title" variants={variants} whileHover={reduceMotion ? undefined : { y: -4 }} transition={{ duration: 0.2 }}>
=======
    .filter(([key, amount]) => !['income', 'savings_transfer'].includes(key) && Number.isFinite(amount) && amount > 0)
    .sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((sum, [, amount]) => sum + amount, 0);
  let offset = 0;
  return <motion.section variants={variants} whileHover={reduceMotion ? undefined : { y: -4 }} transition={{ duration: 0.2 }} className="glass category-spending" aria-labelledby="category-title">
>>>>>>> 44369dbb92b04b4082719636ffeaebb89b05e3c4
    <header className="card-heading"><h2 id="category-title">Gasto por categoría</h2></header>
    {!summary ? <p className="empty">{loading ? 'Cargando gastos…' : 'Gastos no disponibles.'}</p> : total === 0 ? <p className="empty">No hay gastos registrados en este periodo.</p> : <div className="category-content">
      <div className="category-donut">
        <svg viewBox="0 0 160 160" role="img" aria-label={`Gasto total: ${money(total)}. Desglose por categoría en la lista.`}>
          {entries.map(([key, amount], index) => {
            const share = amount / total * 100;
            const start = offset;
            offset += share;
            return <circle key={key} cx="80" cy="80" r="60" pathLength="100" fill="none" stroke={categories[key]?.color || ['#b19bea', '#81b6bc'][index % 2]} strokeWidth="28" strokeDasharray={`${share} ${100 - share}`} strokeDashoffset={-start} transform="rotate(-90 80 80)" />;
          })}
        </svg>
        <div className="category-total"><strong>{money(total)}</strong><span>TOTAL</span></div>
      </div>
      <ul className="category-legend">{entries.map(([key, amount], index) => <li key={key}>
        <span className="category-swatch" style={{ background: categories[key]?.color || ['#b19bea', '#81b6bc'][index % 2] }} />
        <span className="category-name">{categories[key]?.label || key.replaceAll('_', ' ')}</span>
        <strong>{Math.round(amount / total * 100)}%</strong><span className="category-amount">{money(amount)}</span>
      </li>)}</ul>
    </div>}
  </motion.section>;
}
