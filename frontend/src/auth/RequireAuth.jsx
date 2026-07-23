import { Navigate } from 'react-router-dom';
import { useAuth } from './AuthContext';

// Mirrors the old @login_required decorator's behavior — redirect to
// /login if there's no valid session, once we've actually checked (not
// before, or every page would flash a redirect during that first check).
export default function RequireAuth({ children }) {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;
  return children;
}
