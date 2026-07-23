import { apiGet, apiPost } from './client';

export function login(email, password, loginAs) {
  return apiPost('/api/auth/login', { email, password, login_as: loginAs });
}

export function logout() {
  return apiPost('/api/auth/logout');
}

export function me() {
  return apiGet('/api/auth/me');
}

export function switchRole(role) {
  return apiPost('/api/auth/switch-role', { role });
}

export function dashboard() {
  return apiGet('/api/auth/dashboard');
}
