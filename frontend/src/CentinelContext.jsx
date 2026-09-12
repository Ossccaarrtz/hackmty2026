import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { fetchSignals, fetchTransactions, advanceDay } from './api';

const CentinelContext = createContext(null);

function formatDate(isoDate) {
  const [, month, day] = isoDate.split('-').map(Number);
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `${MONTHS[month - 1]} ${day}`;
}

function mapTransaction(t) {
  return {
    name: t.merchant_name || t.category_label || t.description || t.category,
    date: formatDate(t.date),
    amount: t.signed_amount,
  };
}

let seq = 0;
const actionId = () => `action-${Date.now()}-${++seq}`;

export function CentinelProvider({ children }) {
  const [signals, setSignals] = useState(null);
  const [transactions, setTransactions] = useState([]);
  const [balance, setBalance] = useState(null);
  const [actionLog, setActionLog] = useState([]);
  const [loading, setLoading] = useState(true);
  const [liveDataError, setLiveDataError] = useState(false);
  const [advancing, setAdvancing] = useState(false);
  const [done, setDone] = useState(false);

  const loadLiveData = useCallback(() => Promise.all([fetchSignals(), fetchTransactions()]).then(([signalsData, txData]) => {
    setSignals(signalsData);
    setLiveDataError(false);
    setTransactions(txData.transactions.slice().reverse().map(mapTransaction));
    if (txData.transactions.length > 0) {
      setBalance(txData.transactions[txData.transactions.length - 1].running_balance);
    }
    return signalsData;
  }), []);

  useEffect(() => {
    loadLiveData()
      .then((signalsData) => {
        const activeAlert = signalsData.alerts.find((a) => a.status !== 'resolved');
        if (activeAlert) {
          setActionLog([{
            id: actionId(),
            type: 'leak_detected',
            date: 'Today',
            text: `Detected: ${activeAlert.title} ($${activeAlert.annual_cost / 12}/mo) — ${activeAlert.detail} (~$${activeAlert.annual_cost}/yr).`,
            requiresConfirmation: true,
          }]);
        }
      })
      .catch(() => setLiveDataError(true))
      .finally(() => setLoading(false));
  }, [loadLiveData]);

  const dismissAlert = useCallback((alertId) => {
    setSignals((prev) => prev ? {
      ...prev,
      alerts: prev.alerts.map((a) => a.id === alertId ? { ...a, status: 'resolved' } : a),
    } : prev);
  }, []);

  const handleAdvanceDay = useCallback(() => {
    setAdvancing(true);
    return advanceDay()
      .then((result) => {
        if (result.done) {
          setDone(true);
          return;
        }
        const mapped = (result.new_actions || []).map((a) => ({
          id: actionId(),
          type: a.type,
          date: result.label || result.date,
          text: a.text,
          requiresConfirmation: !!a.requires_confirmation,
        }));
        setActionLog((prev) => [...mapped, ...prev]);
        return loadLiveData();
      })
      .catch(() => setLiveDataError(true))
      .finally(() => setAdvancing(false));
  }, [loadLiveData]);

  const value = {
    signals, transactions, balance, actionLog, loading, liveDataError, advancing, done,
    dismissAlert, handleAdvanceDay,
  };

  return <CentinelContext.Provider value={value}>{children}</CentinelContext.Provider>;
}

export function useCentinel() {
  const ctx = useContext(CentinelContext);
  if (!ctx) throw new Error('useCentinel must be used within CentinelProvider');
  return ctx;
}
