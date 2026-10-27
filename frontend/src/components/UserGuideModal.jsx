import React, { useEffect, useMemo, useRef } from 'react';
import Markdown, { parseBlocks, slugify } from './Markdown';
import ScreenMap from './ScreenMap';
import { GUIDE_MARKDOWN, GUIDE_TITLE, GUIDE_VERSION } from '../userGuide';

const FONT = 'IBM Plex Sans, sans-serif';
const MONO = 'IBM Plex Mono, monospace';
const SEEN_KEY = 'nr.userGuideSeen';

// "New to the system" is per browser, not per account: many people share one
// viewer login, so the account cannot tell one person's first visit from the
// next. A version bump in userGuide.js shows a revised guide once more.
export function guideSeen() {
  try { return Number(localStorage.getItem(SEEN_KEY)) >= GUIDE_VERSION; }
  catch (e) { return true; }   // storage blocked: don't pop up on every sign-in
}

export function markGuideSeen() {
  try { localStorage.setItem(SEEN_KEY, String(GUIDE_VERSION)); } catch (e) { /* ignore */ }
}

/**
 * The user guide as a dialog: contents on the left, the guide on the right.
 * `welcome` adds the first-sign-in note and a "Got it" button.
 */
export default function UserGuideModal({ onClose, welcome = false }) {
  const contentRef = useRef(null);
  const sections = useMemo(
    () => parseBlocks(GUIDE_MARKDOWN).filter(b => b.type === 'heading' && b.level === 2),
    []);

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const jump = (text) => {
    const el = contentRef.current?.querySelector(`[id="${slugify(text)}"]`);
    el?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  return (
    <div
      onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}
      style={{
        position: 'fixed', inset: 0, zIndex: 2000,
        background: 'rgba(0,0,0,0.75)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontFamily: FONT,
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={GUIDE_TITLE}
        style={{
          width: 'min(1100px, 94vw)', height: '88vh',
          display: 'flex', flexDirection: 'column',
          background: '#0d1015', border: '1px solid #2a3340', borderRadius: 8,
          boxShadow: '0 16px 48px rgba(0,0,0,0.6)', overflow: 'hidden',
          animation: 'fadeIn 0.2s ease',
        }}
      >
        {/* Header */}
        <div style={{ flexShrink: 0, display: 'flex', alignItems: 'center', gap: 12, padding: '14px 20px', borderBottom: '1px solid #1e2530', background: '#111418' }}>
          <div style={{ width: 22, height: 22, borderRadius: '50%', background: 'radial-gradient(circle at 35% 35%, #00d4ff, #005566)', flexShrink: 0 }} />
          <h2 style={{ fontSize: 16, fontWeight: 600, color: '#e8edf2', margin: 0, flex: 1 }}>{GUIDE_TITLE}</h2>
          <button
            onClick={onClose}
            title="Close (Esc)"
            style={{ background: 'transparent', border: '1px solid #2a3340', borderRadius: 4, color: '#7a8a99', fontSize: 12, padding: '5px 10px', cursor: 'pointer', fontFamily: FONT }}
          >
            ✕ Close
          </button>
        </div>

        {welcome && (
          <div style={{ flexShrink: 0, padding: '10px 20px', background: '#0d1a2a', borderBottom: '1px solid #00d4ff22', fontSize: 13, color: '#b0c4d4', lineHeight: 1.5 }}>
            <strong style={{ color: '#e8edf2' }}>Welcome.</strong> This guide opens once, the first time you sign in on this computer.
            Open it again any time with <strong style={{ color: '#00d4ff' }}>? Guide</strong> in the top bar.
          </div>
        )}

        <div style={{ flex: 1, minHeight: 0, display: 'flex' }}>
          {/* Contents */}
          <nav style={{ flex: '0 0 230px', overflowY: 'auto', padding: '16px 10px 16px 16px', borderRight: '1px solid #1e2530', background: '#0a0c10' }}>
            <div style={{ fontSize: 10, color: '#4a5568', fontFamily: MONO, letterSpacing: '0.08em', marginBottom: 8 }}>CONTENTS</div>
            {sections.map(s => (
              <button
                key={s.text}
                onClick={() => jump(s.text)}
                style={{ display: 'block', width: '100%', textAlign: 'left', background: 'none', border: 'none', color: '#9fb0c0', fontSize: 12.5, padding: '5px 6px', borderRadius: 4, cursor: 'pointer', fontFamily: FONT, lineHeight: 1.35 }}
                onMouseEnter={e => { e.currentTarget.style.background = '#13161c'; e.currentTarget.style.color = '#e8edf2'; }}
                onMouseLeave={e => { e.currentTarget.style.background = 'none'; e.currentTarget.style.color = '#9fb0c0'; }}
              >
                {s.text}
              </button>
            ))}
          </nav>

          {/* Guide */}
          <div ref={contentRef} style={{ flex: 1, minWidth: 0, overflowY: 'auto', padding: '4px 32px 32px' }}>
            <Markdown source={GUIDE_MARKDOWN} embeds={{ SCREEN_MAP: <ScreenMap /> }} />
          </div>
        </div>

        {welcome && (
          <div style={{ flexShrink: 0, display: 'flex', justifyContent: 'flex-end', padding: '10px 20px', borderTop: '1px solid #1e2530', background: '#111418' }}>
            <button
              onClick={onClose}
              style={{ padding: '7px 22px', background: '#00d4ff', color: '#0a0c10', border: 'none', borderRadius: 4, fontSize: 13, fontWeight: 600, cursor: 'pointer', fontFamily: FONT }}
            >
              Got it
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
