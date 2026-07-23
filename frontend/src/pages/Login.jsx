import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loginAs, setLoginAs] = useState('Client User');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    setError('');
    setSubmitting(true);
    try {
      const user = await login(email, password, loginAs);
      navigate(user.role === 'Admin' ? '/admin' : '/dashboard', { replace: true });
    } catch (err) {
      setError(err.message || 'Invalid email or password.');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div style={styles.page}>
      <form onSubmit={handleSubmit} style={styles.card}>
        <div style={styles.brand}>DROGO AEROSPACE</div>
        <h1 style={styles.title}>Sign in</h1>

        <div style={styles.roleToggle}>
          {['Client User', 'Admin'].map(role => (
            <label key={role} style={{ ...styles.roleOption, ...(loginAs === role ? styles.roleOptionActive : {}) }}>
              <input
                type="radio" name="login_as" value={role} checked={loginAs === role}
                onChange={() => setLoginAs(role)} style={{ display: 'none' }}
              />
              {role}
            </label>
          ))}
        </div>

        <label style={styles.label}>Email</label>
        <input
          type="email" required value={email} onChange={e => setEmail(e.target.value)}
          placeholder="you@company.com" style={styles.input}
        />

        <label style={styles.label}>Password</label>
        <input
          type="password" required value={password} onChange={e => setPassword(e.target.value)}
          placeholder="••••••••" style={styles.input}
        />

        {error && <div style={styles.error}>{error}</div>}

        <button type="submit" disabled={submitting} className="btn-primary" style={styles.submit}>
          {submitting ? 'Signing in…' : 'Sign In'}
        </button>
      </form>
    </div>
  );
}

const styles = {
  page: { minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg-base)' },
  card: {
    width: '100%', maxWidth: 380, background: 'var(--bg-panel)', border: '1px solid var(--border)',
    borderRadius: 'var(--radius-lg)', padding: '36px 32px', boxShadow: '0 10px 40px rgba(0,0,0,.06)',
  },
  brand: { fontSize: 11, letterSpacing: '.15em', color: 'var(--text-muted)', fontWeight: 700, marginBottom: 6 },
  title: { fontSize: 22, fontWeight: 800, color: 'var(--text-primary)', marginBottom: 24 },
  roleToggle: { display: 'flex', gap: 8, marginBottom: 22, background: 'var(--bg-elevated-2)', borderRadius: 'var(--radius-sm)', padding: 4 },
  roleOption: {
    flex: 1, textAlign: 'center', padding: '8px 0', borderRadius: 6, fontSize: 12.5, fontWeight: 700,
    color: 'var(--text-muted)', cursor: 'pointer',
  },
  roleOptionActive: { background: 'var(--bg-panel)', color: 'var(--accent)', boxShadow: '0 1px 3px rgba(0,0,0,.08)' },
  label: { display: 'block', fontSize: 12, fontWeight: 700, color: 'var(--text-secondary)', marginBottom: 6, marginTop: 14 },
  input: {
    width: '100%', padding: '11px 14px', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)',
    fontSize: 14, background: 'var(--bg-elevated-2)', color: 'var(--text-primary)', outline: 'none',
  },
  error: { marginTop: 14, fontSize: 12.5, color: 'var(--danger)', background: 'rgba(201,75,66,.08)', padding: '8px 12px', borderRadius: 8 },
  submit: { width: '100%', marginTop: 22, padding: '12px 0', fontSize: 14 },
};
