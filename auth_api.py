"""
auth_api.py — JSON authentication endpoints for the decoupled SPA frontend.

The existing /login, /logout etc. routes in app.py stay exactly as they are
(server-rendered, form-POST, used by the legacy Jinja templates) — nothing
about them changes. This is a PARALLEL, additive API surface for the new
React frontend: same underlying user_store.authenticate()/session logic,
just returning JSON instead of redirects/rendered HTML, so a JS app can
drive it with fetch() instead of a full-page form submit.

Session-cookie auth is kept (not switched to JWT) specifically so the
frontend can be served same-origin behind the same domain/proxy as this
API in production, and via Vite's dev-server proxy in development — no
CORS or cross-site-cookie configuration needed either way. See
frontend/vite.config.js.
"""
from datetime import datetime
from flask import Blueprint, request, jsonify, session, url_for

import users as user_store
from models import db, ActivityLog

auth_api_bp = Blueprint('auth_api_bp', __name__, url_prefix='/api/auth')


def _session_user_payload():
    return {
        'id':          session.get('user_id'),
        'username':    session.get('user_name'),
        'email':       session.get('user_email'),
        'role':        session.get('role'),
        'all_roles':   session.get('all_roles', []),
        'modules':     session.get('modules', []),
    }


@auth_api_bp.route('/login', methods=['POST'])
def api_login():
    data = request.get_json(force=True, silent=True) or {}
    email    = (data.get('email') or '').strip()
    password = data.get('password') or ''
    login_as = data.get('login_as') or 'Client User'

    user, error = user_store.authenticate(email, password, login_as=login_as)
    if not user:
        return jsonify({'error': error or 'Invalid email or password.'}), 401

    session['user_id']    = user['id']
    session['user_name']  = user['username']
    session['user_email'] = user['email']
    session['role']       = login_as
    session['all_roles']  = user['roles']
    session['modules']    = user['modules']
    session['login_at']   = datetime.utcnow().isoformat()

    user_store.record_login(user['id'])
    ActivityLog.log(action='login', entity_type='User', entity_name=user['username'],
                     performed_by=user['username'], role=login_as, details=f'Logged in as {login_as}')
    db.session.commit()

    return jsonify(_session_user_payload())


@auth_api_bp.route('/logout', methods=['POST'])
def api_logout():
    if 'user_id' in session and 'login_at' in session:
        try:
            started = datetime.fromisoformat(session['login_at'])
            duration = (datetime.utcnow() - started).total_seconds()
            ActivityLog.log(action='logout', entity_type='User', entity_name=session.get('user_name', ''),
                             performed_by=session.get('user_name', ''), role=session.get('role', ''),
                             duration_seconds=duration, details='Logged out')
            db.session.commit()
        except Exception:
            pass
    session.clear()
    return jsonify({'ok': True})


@auth_api_bp.route('/me', methods=['GET'])
def api_me():
    """Called once on frontend load to check "is there already a valid
    session cookie" — lets the SPA restore a logged-in state on refresh
    without the user re-entering credentials, the same thing the old
    server-rendered pages got for free from session cookies."""
    if 'user_id' not in session:
        return jsonify({'user': None})
    return jsonify({'user': _session_user_payload()})


@auth_api_bp.route('/switch-role', methods=['POST'])
def api_switch_role():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in.'}), 401
    data = request.get_json(force=True, silent=True) or {}
    target = data.get('role')
    if target not in ('Admin', 'Client User'):
        return jsonify({'error': 'role must be Admin or Client User.'}), 400
    if target not in session.get('all_roles', []):
        return jsonify({'error': f"This account doesn't have {target} access."}), 403
    session['role'] = target
    return jsonify(_session_user_payload())


@auth_api_bp.route('/dashboard', methods=['GET'])
def api_dashboard():
    """The module cards a logged-in Client User sees on their home screen
    — same data client_dashboard() has always built for the server-rendered
    template, just as JSON. Each card's `route` is a Flask endpoint name;
    the frontend is responsible for mapping that to its own client-side
    route (see frontend/src/moduleRoutes.js)."""
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in.'}), 401

    assigned = session.get('modules', [])
    cards = []
    for m in assigned:
        route = user_store.MODULE_ROUTES.get(m)
        if not route:
            continue
        try:
            url = url_for(route)
        except Exception:
            continue  # a module's target route doesn't exist/isn't resolvable — skip rather than send a dead link
        meta = user_store.MODULE_CARDS.get(m, {})
        cards.append({
            'name': m,
            'url': url,
            'image': meta.get('image'),
            'accent': meta.get('accent', '#b7caf1'),
            'desc': meta.get('desc'),
        })
    return jsonify({
        'user': _session_user_payload(),
        'modules': cards,
        'can_switch_admin': 'Admin' in session.get('all_roles', []),
    })
