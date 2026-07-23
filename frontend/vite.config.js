import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Flask backend's address during local development. In production, this
// frontend is built to static files (`npm run build`) and served from
// behind the SAME origin as the API (either by Flask itself, or a reverse
// proxy in front of both) — so this proxy only matters for `npm run dev`.
const BACKEND_URL = process.env.BACKEND_URL || 'http://127.0.0.1:5000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Every API namespace this app currently has, proxied through so the
      // browser sees it all as same-origin — this is what lets the existing
      // Flask session cookie keep working unchanged with zero CORS setup.
      '/api':      { target: BACKEND_URL, changeOrigin: true },
      '/static':   { target: BACKEND_URL, changeOrigin: true },
      '/uploads':  { target: BACKEND_URL, changeOrigin: true },
    },
  },
});
