import { useEffect, useState } from 'react';
import { useAuth } from '../auth/AuthContext';
import { dashboard } from '../api/auth';
import { moduleHref } from '../moduleRoutes';

export default function Dashboard() {
  const { user, logout } = useAuth();
  const [data, setData] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    dashboard().then(setData).catch(err => setError(err.message));
  }, []);

  return (
    <div style={styles.page}>
      <nav style={styles.navbar}>
        <div style={styles.brand}>DROGO AEROSPACE</div>
        <div style={styles.navRight}>
          <span style={styles.welcome}>Welcome, <strong>{user?.username?.split(' ')[0]}</strong></span>
          <button className="btn-ghost" onClick={logout}>Logout</button>
        </div>
      </nav>

      <div style={styles.content}>
        <h1 style={styles.title}>Your Modules</h1>
        <p style={styles.sub}>Select a module to get started.</p>

        {error && <div style={styles.error}>{error}</div>}

        <div style={styles.grid}>
          {data?.modules?.map(m => (
            // These module pages haven't been migrated to this frontend
            // yet — this deliberately links out to the existing
            // server-rendered page for now (see moduleRoutes.js), so
            // every module keeps working throughout the migration
            // instead of breaking the moment this page goes live.
            <a key={m.name} href={moduleHref(m.url)} style={styles.card}>
              <div style={{ ...styles.cardImg, backgroundImage: `url(${m.image})` }} />
              <div style={styles.cardBody}>
                <div style={{ ...styles.cardAccent, background: m.accent }} />
                <div style={styles.cardName}>{m.name}</div>
                <div style={styles.cardDesc}>{m.desc}</div>
              </div>
            </a>
          ))}
          {data && !data.modules?.length && (
            <div style={styles.empty}>No modules have been assigned to your account yet. Please contact your administrator.</div>
          )}
        </div>
      </div>
    </div>
  );
}

const styles = {
  page: { minHeight: '100vh', background: 'var(--bg-base)' },
  navbar: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 28px',
    borderBottom: '1px solid var(--border-soft)', background: 'var(--bg-panel)',
  },
  brand: { fontSize: 13, fontWeight: 800, letterSpacing: '.05em', color: 'var(--text-primary)' },
  navRight: { display: 'flex', alignItems: 'center', gap: 16 },
  welcome: { fontSize: 13, color: 'var(--text-secondary)' },
  content: { padding: '32px 40px' },
  title: { fontSize: 22, fontWeight: 800, color: 'var(--text-primary)' },
  sub: { fontSize: 13, color: 'var(--text-muted)', marginTop: 4, marginBottom: 24 },
  error: { fontSize: 13, color: 'var(--danger)', marginBottom: 16 },
  grid: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 20 },
  card: {
    borderRadius: 'var(--radius-lg)', overflow: 'hidden', border: '1px solid var(--border)',
    textDecoration: 'none', background: 'var(--bg-elevated)', display: 'block',
  },
  cardImg: { height: 120, backgroundSize: 'cover', backgroundPosition: 'center' },
  cardBody: { padding: '16px 18px' },
  cardAccent: { width: 28, height: 4, borderRadius: 4, marginBottom: 10 },
  cardName: { fontSize: 15, fontWeight: 800, color: 'var(--text-primary)' },
  cardDesc: { fontSize: 12.5, color: 'var(--text-muted)', marginTop: 6, lineHeight: 1.5 },
  empty: { gridColumn: '1/-1', padding: 40, textAlign: 'center', color: 'var(--text-muted)', fontSize: 13 },
};
