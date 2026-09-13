import React, { useState } from 'react';
import { motion, useReducedMotion } from 'framer-motion';

// Orden fijo, nunca reasignado por monto/rango -- paleta categorica validada
// (ver dataviz skill: separacion CVD y contraste normal-vision, las 5
// primeras posiciones de un orden de 8 que pasa todos los checks).
const categories = {
  rent: { label: 'Renta', color: '#2a78d6' },
  groceries: { label: 'Supermercado', color: '#eb6834' },
  transport: { label: 'Transporte', color: '#1baf7a' },
  utilities: { label: 'Servicios', color: '#eda100' },
  discretionary: { label: 'Discrecional', color: '#e87ba4' },
};
const FALLBACK_COLORS = ['#4a3aa7', '#e34948'];
const money = value => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value);
const GAP = 0.6; // en unidades de pathLength (100 = circunferencia completa) -- separa segmentos tocandose

export default function CategorySpending({ summary, loading, variants }) {
  const reduceMotion = useReducedMotion();
  const [hovered, setHovered] = useState(null);
  const entries = Object.entries(summary?.by_category || {})
    .filter(([key, amount]) => !['income', 'savings_transfer'].includes(key) && !key.startsWith('envelope:') && Number.isFinite(amount) && amount > 0)
    .sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((sum, [, amount]) => sum + amount, 0);
  const hoveredEntry = hovered ? entries.find(([key]) => key === hovered) : null;
  let offset = 0;
  return <motion.section className="glass category-spending" aria-labelledby="category-title" variants={variants} whileHover={reduceMotion ? undefined : { y: -4 }} transition={{ duration: 0.2 }}>
    <header className="card-heading"><h2 id="category-title">Gasto por categoría</h2></header>
    {!summary ? <p className="empty">{loading ? 'Cargando gastos…' : 'Gastos no disponibles.'}</p> : total === 0 ? <p className="empty">No hay gastos registrados en este periodo.</p> : <div className="category-content">
      <div className="category-donut" onMouseLeave={() => setHovered(null)}>
        <svg viewBox="0 0 160 160" role="img" aria-label={`Gasto total: ${money(total)}. Desglose por categoría en la lista.`}>
          {entries.map(([key, amount], index) => {
            const share = amount / total * 100;
            const start = offset;
            offset += share;
            const visible = Math.max(share - GAP, 0.001);
            return <circle key={key} cx="80" cy="80" r="60" pathLength="100"
              className={`category-arc ${hovered ? (hovered === key ? 'is-hovered' : 'is-dimmed') : ''}`}
              fill="none" stroke={categories[key]?.color || FALLBACK_COLORS[index % FALLBACK_COLORS.length]}
              strokeWidth="28" strokeDasharray={`${visible} ${100 - visible}`} strokeDashoffset={-start}
              transform="rotate(-90 80 80)"
              onMouseEnter={() => setHovered(key)} onFocus={() => setHovered(key)} onBlur={() => setHovered(null)}
              tabIndex={0} aria-label={`${categories[key]?.label || key}: ${money(amount)}, ${Math.round(share)}%`} />;
          })}
        </svg>
        <div className="category-total">
          {hoveredEntry
            ? <><strong>{money(hoveredEntry[1])}</strong><span>{categories[hoveredEntry[0]]?.label || hoveredEntry[0]} · {Math.round(hoveredEntry[1] / total * 100)}%</span></>
            : <><strong>{money(total)}</strong><span>TOTAL</span></>}
        </div>
      </div>
      <ul className="category-legend">{entries.map(([key, amount], index) => <li key={key}
        className={hovered === key ? 'is-hovered' : ''}
        onMouseEnter={() => setHovered(key)} onMouseLeave={() => setHovered(null)}
        onFocus={() => setHovered(key)} onBlur={() => setHovered(null)} tabIndex={0}>
        <span className="category-swatch" style={{ background: categories[key]?.color || FALLBACK_COLORS[index % FALLBACK_COLORS.length] }} />
        <span className="category-name">{categories[key]?.label || key.replaceAll('_', ' ')}</span>
        <strong>{Math.round(amount / total * 100)}%</strong><span className="category-amount">{money(amount)}</span>
      </li>)}</ul>
    </div>}
  </motion.section>;
}
