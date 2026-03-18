import React, { useState } from 'react';
import { login, getMe } from '../api';
import { useAppStore } from '../store';

export default function LoginPage({ onSuccess }) {
  const { setToken, setUser } = useAppStore();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      const res = await login(username, password);
      setToken(res.data.access_token);
      setUser({ username: res.data.username, role: res.data.role });
      onSuccess?.();
    } catch (err) {
      setError('Invalid username or password');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{
      width: '100%', height: '100%',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: '#0a0c10',
      fontFamily: 'IBM Plex Sans, sans-serif',
    }}>
      {/* Subtle grid background */}
      <div style={{
        position: 'absolute', inset: 0,
        backgroundImage: 'linear-gradient(#1e253011 1px, transparent 1px), linear-gradient(90deg, #1e253011 1px, transparent 1px)',
        backgroundSize: '40px 40px',
        pointerEvents: 'none',
      }} />

      <div style={{
        width: 380,
        background: '#111418',
        border: '1px solid #1e2530',
        borderRadius: 8,
        padding: '36px 32px',
        animation: 'fadeIn 0.3s ease',
        position: 'relative',
        zIndex: 1,
      }}>
        {/* Logo */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 32 }}>
          <div style={{
            width: 36, height: 36, borderRadius: '50%',
            background: 'radial-gradient(circle at 35% 35%, #00d4ff, #005566)',
            flexShrink: 0,
          }} />
          <div>
            <div style={{ fontSize: 16, fontWeight: 600, color: '#e8edf2', letterSpacing: '0.02em' }}>
              NeuroReconstruct
            </div>
            <div style={{ fontSize: 11, color: '#4a5568', marginTop: 2 }}>
              sEEG / ECoG Brain Viewer
            </div>
          </div>
        </div>

        <form onSubmit={handleSubmit}>
          <div style={{ marginBottom: 16 }}>
            <label style={{ display: 'block', fontSize: 11, color: '#7a8a99', marginBottom: 6, letterSpacing: '0.05em' }}>
              USERNAME
            </label>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="username"
              autoComplete="username"
              required
            />
          </div>

          <div style={{ marginBottom: 24 }}>
            <label style={{ display: 'block', fontSize: 11, color: '#7a8a99', marginBottom: 6, letterSpacing: '0.05em' }}>
              PASSWORD
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              autoComplete="current-password"
              required
            />
          </div>

          {error && (
            <div style={{
              padding: '8px 12px',
              background: '#2a0d0d',
              border: '1px solid #5a1a1a',
              borderRadius: 4,
              color: '#ff5252',
              fontSize: 12,
              marginBottom: 16,
            }}>
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            style={{
              width: '100%',
              padding: '10px',
              background: loading ? '#002233' : '#00d4ff',
              color: loading ? '#00d4ff' : '#0a0c10',
              border: 'none',
              borderRadius: 4,
              fontSize: 13,
              fontWeight: 600,
              cursor: loading ? 'not-allowed' : 'pointer',
              transition: 'all 0.15s',
              fontFamily: 'IBM Plex Sans, sans-serif',
            }}
          >
            {loading ? 'Signing in...' : 'Sign In'}
          </button>
        </form>

        <div style={{ marginTop: 20, padding: '12px', background: '#0d1015', borderRadius: 4, border: '1px solid #1a1e24' }}>
          <div style={{ fontSize: 10, color: '#4a5568', lineHeight: 1.6 }}>
            <strong style={{ color: '#7a8a99' }}>Access restricted.</strong> This system contains de-identified patient neuroimaging data.
            Unauthorized access is prohibited.
          </div>
        </div>
      </div>
    </div>
  );
}
