import React, { useEffect, useState } from 'react';
import './index.css';
import { useAppStore } from './store';
import { getMe } from './api';
import LoginPage from './components/LoginPage';
import Header from './components/Header';
import ReconstructionList from './components/ReconstructionList';
import ReconstructionViewer from './components/ReconstructionViewer';
import DeletedList from './components/DeletedList';

export default function App() {
  const { user, setUser, token, logout, setReconstruction, setMeshData } = useAppStore();
  const [page, setPage] = useState('list'); // 'list' | 'viewer' | 'login' | 'deleted'
  const [selectedReconId, setSelectedReconId] = useState(null);
  const [shareToken, setShareToken] = useState(null);
  const [authLoading, setAuthLoading] = useState(true);

  // Parse URL for share-link access: /view/:id?token=xxx
  useEffect(() => {
    const path = window.location.pathname;
    const params = new URLSearchParams(window.location.search);
    const match = path.match(/^\/view\/(\d+)/);
    if (match) {
      setSelectedReconId(parseInt(match[1]));
      setShareToken(params.get('token'));
      setPage('viewer');
    }
  }, []);

  // Restore session from stored token
  useEffect(() => {
    if (!token) {
      setAuthLoading(false);
      return;
    }
    getMe()
      .then(res => setUser(res.data))
      .catch(() => logout())
      .finally(() => setAuthLoading(false));
  }, []);

  const navigateTo = (path) => {
    if (path === '/login') setPage('login');
    if (path === '/list') { setPage('list'); setSelectedReconId(null); setReconstruction(null); setMeshData(null); }
  };

  const handleSelectReconstruction = (id) => {
    setSelectedReconId(id);
    setPage('viewer');
    window.history.pushState({}, '', `/view/${id}`);
  };

  const handleBack = () => {
    setPage('list');
    setSelectedReconId(null);
    setReconstruction(null);
    setMeshData(null);
    window.history.pushState({}, '', '/');
  };

  if (authLoading) {
    return (
      <div style={{
        width: '100%', height: '100%',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: '#0a0c10', color: '#4a5568',
        fontFamily: 'IBM Plex Sans, sans-serif', fontSize: 12,
      }}>
        Loading...
      </div>
    );
  }

  // Share-link viewer: no login required
  if (page === 'viewer' && shareToken && !user) {
    return (
      <div style={{ width: '100%', height: '100%', display: 'flex', flexDirection: 'column' }}>
        <Header onNavigate={navigateTo} />
        <ReconstructionViewer reconId={selectedReconId} shareToken={shareToken} />
      </div>
    );
  }

  // Require login for everything else
  if (!user && page !== 'login') {
    return <LoginPage onSuccess={() => setPage('list')} />;
  }

  if (page === 'login') {
    return <LoginPage onSuccess={() => setPage('list')} />;
  }

  return (
    <div style={{ width: '100%', height: '100%', display: 'flex', flexDirection: 'column' }}>
      <Header
        onBack={page !== 'list' ? handleBack : null}
        onNavigate={navigateTo}
      />

      <div style={{ flex: 1, display: 'flex', overflow: 'auto', minWidth: 0 }}>
        {page === 'list' && (
          <ReconstructionList
            onSelect={handleSelectReconstruction}
            onTrash={() => setPage('deleted')}
          />
        )}
        {page === 'viewer' && (
          <ReconstructionViewer reconId={selectedReconId} shareToken={shareToken} />
        )}
        {page === 'deleted' && (
          <DeletedList onBack={() => setPage('list')} />
        )}
      </div>
    </div>
  );
}
