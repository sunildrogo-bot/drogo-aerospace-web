# DROGO AEROSPACE — Frontend (React SPA)

This is the new, decoupled frontend, talking to the existing Flask backend
purely over JSON APIs. It replaces the server-rendered Jinja templates
**incrementally** — see "Migration strategy" below.

## Architecture decision

We're **not** splitting the backend into microservices. With one team and
no current scaling pressure, that would add real operational cost
(service discovery, distributed auth, network failure handling, running
multiple processes instead of one) with no matching benefit right now.

Instead: the backend stays **one service**, already organized into
modules via Flask blueprints (`chimney_routes.py`, `projects_routes.py`,
`users_routes.py`, etc.) — a "modular monolith." This frontend is a
**separate, independently-run app** that talks to it only through the
JSON API. If a real need for microservices ever shows up, those module
boundaries are already there to split along.

## Local development

Two processes, run separately:

```bash
# Terminal 1 — the existing Flask backend, unchanged
cd ..
python app.py          # runs on http://127.0.0.1:5000

# Terminal 2 — this frontend
cd frontend
npm install
npm run dev             # runs on http://localhost:5173
```

Open `http://localhost:5173`. Vite's dev server proxies `/api`,
`/static`, and `/uploads` requests to the Flask backend (see
`vite.config.js`), so the browser sees everything as same-origin — the
existing session cookie just works, no CORS setup needed.

## Production

```bash
npm run build
```

produces static files in `dist/`. Deploy these behind the **same origin**
as the Flask API (either served by Flask itself via `send_from_directory`,
or a reverse proxy in front of both) and the session cookie keeps working
exactly the same way, no code changes needed.

## Migration strategy

Converting all ~20 existing pages in one shot isn't realistic or safe for
an app already in real use. Instead:

1. **This slice**: Login + the Client Dashboard (`/dashboard`) are fully
   migrated. Every module card on the dashboard still links **out** to
   the existing server-rendered pages (`moduleRoutes.js` handles this) —
   nothing else breaks.
2. **Next**: pick one module (Admin panel, or one of Chimney/TRANS/Land
   Survey/Transmission Line) and migrate its pages the same way — new
   React pages calling the *same* existing JSON API endpoints those pages
   already use (most of this backend was already API-driven before this
   change).
3. Repeat until every page has a React equivalent, then retire the old
   Jinja templates.

At every step, anything not yet migrated keeps working via a full-page
link to its existing URL — the app is never in a broken in-between state.

## What changed on the backend

- `auth_api.py` — new, additive JSON endpoints (`/api/auth/login`,
  `/logout`, `/me`, `/switch-role`, `/dashboard`) that reuse the exact
  same `user_store.authenticate()` / session logic the old `/login` route
  already used. The old form-POST `/login` route is untouched and still
  works for any page that hasn't migrated yet.
- Nothing else changed. Every existing API endpoint (chimney defects,
  TRANS photos/reports, users, projects) was already JSON — this
  frontend can start calling those directly as each page migrates.
