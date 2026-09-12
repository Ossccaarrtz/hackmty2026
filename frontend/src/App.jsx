import React from 'react';
import { Routes, Route } from 'react-router-dom';
import { CentinelProvider } from './CentinelContext';
import Dashboard from './pages/Dashboard';
import Chat from './pages/Chat';

export default function App() {
  return (
    <CentinelProvider>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/chat" element={<Chat />} />
      </Routes>
    </CentinelProvider>
  );
}
