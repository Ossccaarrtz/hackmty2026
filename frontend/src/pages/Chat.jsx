import React, { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { ChevronLeft, Sparkles } from 'lucide-react';
import { useCentinel } from '../CentinelContext';

export default function Chat() {
  const navigate = useNavigate();
  const { actionLog, advancing, done, handleAdvanceDay } = useCentinel();
  const bodyRef = useRef(null);
  const chronological = actionLog.slice().reverse();
  const lastAction = chronological[chronological.length - 1];
  const pendingConfirmation = lastAction?.requiresConfirmation;

  useEffect(() => {
    bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight, behavior: 'smooth' });
  }, [actionLog]);

  return (
    <div className="chat-screen">
      <header className="chat-header">
        <button className="chat-back" aria-label="Back to dashboard" onClick={() => navigate('/')}><ChevronLeft size={20} /></button>
        <span className="chat-avatar"><Sparkles size={16} /></span>
        <div><strong>Centinel One</strong><p>AI Financial Coach</p></div>
      </header>

      <div className="chat-body" ref={bodyRef}>
        {chronological.length === 0 && <p className="empty-note">No messages yet — advance the day to hear from your agent.</p>}
        {chronological.map((a) => (
          <div className="chat-bubble agent" key={a.id}>
            <p>{a.text}</p>
            <span className="chat-bubble-date">{a.date}</span>
          </div>
        ))}
      </div>

      <div className="chat-controls">
        {done
          ? <p className="empty-note">No more demo days to advance.</p>
          : <>
              {pendingConfirmation && <p className="chat-hint">This action requires your confirmation before Centinel One acts.</p>}
              <button className="black-button" disabled={advancing} onClick={handleAdvanceDay}>
                {advancing ? 'Advancing…' : pendingConfirmation ? 'Confirm & continue' : 'Advance day'}
              </button>
            </>}
      </div>
    </div>
  );
}
