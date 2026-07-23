// Modules that haven't been migrated to this frontend yet are still
// full-page links out to the existing server-rendered Flask pages —
// that's the bridge strategy for migrating incrementally instead of
// having to convert all ~20 pages before any of this can go live.
//
// The backend resolves each module's real URL with Flask's own url_for()
// (see /api/auth/dashboard in auth_api.py), so this never has to guess or
// duplicate Flask's routing table. The only thing needed here is: in
// dev, Vite runs on a different port than Flask, so a plain relative link
// would try to load from Vite's own origin (which doesn't have these
// pages) instead of the backend. In production this frontend is built
// and served from the SAME origin as the API, so the relative URL just
// works as-is with no adjustment needed.
const DEV_BACKEND_ORIGIN = 'http://127.0.0.1:5000';

export function moduleHref(url) {
  if (import.meta.env.DEV) return DEV_BACKEND_ORIGIN + url;
  return url;
}
