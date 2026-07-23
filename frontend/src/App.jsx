import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './auth/AuthContext';
import RequireAuth from './auth/RequireAuth';
import Login from './pages/Login';
import Dashboard from './pages/Dashboard';
import LegacyRedirect from './pages/LegacyRedirect';

function HomeRedirect() {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;
  return <Navigate to={user.role === 'Admin' ? '/admin' : '/dashboard'} replace />;
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/" element={<HomeRedirect />} />
          <Route path="/login" element={<Login />} />
          <Route
            path="/dashboard"
            element={
              <RequireAuth>
                <Dashboard />
              </RequireAuth>
            }
          />
          {/* Admin panel isn't migrated yet — bounces out to the existing
              server-rendered page. Remove this once Admin gets its own
              React route. */}
          <Route
            path="/admin"
            element={
              <RequireAuth>
                <LegacyRedirect to="/admin" />
              </RequireAuth>
            }
          />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
