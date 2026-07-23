import os
import time
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from functools import wraps
import json
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()
# ── App factory ───────────────────────────────────────────────────────────────

def create_app(db_url: str | None = None) -> Flask:
    app = Flask(__name__)

    # ── Config ────────────────────────────────────────────────────────────────
    app.secret_key = os.environ.get('SECRET_KEY', 'nova-plus-dev-secret-CHANGE-IN-PROD')

    # SQLite by default; override with DATABASE_URL env var for PostgreSQL/MySQL
    base_dir = os.path.abspath(os.path.dirname(__file__))
    default_db = f"sqlite:///{os.path.join(base_dir, 'instance', 'nova.db')}"
    app.config['SQLALCHEMY_DATABASE_URI'] = db_url or os.environ.get('DATABASE_URL', default_db)
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # ── Extensions ────────────────────────────────────────────────────────────
    from models import db, ActivityLog
    db.init_app(app)

    # ── Live portal load tracking — see load_monitor.py for the honest
    #    single-process caveat. Times every single request; deliberately
    #    lightweight (in-memory, no DB write per request) so this doesn't
    #    become load of its own.
    from load_monitor import record_request

    # The monitoring endpoints themselves (polled every 1s by the dashboard's
    # own live chart) must NOT be counted as "load" — otherwise the monitor
    # measures its own polling traffic rather than real portal usage. Just
    # having the dashboard open would guarantee 60 requests/minute from this
    # alone, which was already enough to push the badge into "moderate"
    # (>40/min) or even "high" (>120/min with a couple of tabs open) with
    # zero actual user activity anywhere else in the app.
    LOAD_MONITOR_EXCLUDED_PATHS = {
        '/api/dashboard/live-load',
        '/api/dashboard/live-load-history',
    }

    @app.before_request
    def _load_monitor_start():
        request._load_monitor_start_time = time.time()

    @app.after_request
    def _load_monitor_end(response):
        start = getattr(request, '_load_monitor_start_time', None)
        if start is not None:
            record_request(
                (time.time() - start) * 1000,
                user_id=session.get('user_id'),
                count_toward_load=request.path not in LOAD_MONITOR_EXCLUDED_PATHS,
            )
        return response

    from flask_migrate import Migrate
    Migrate(app, db)

    # Self-healing schema check: adds any new chimney_defects columns this
    # version of the app needs (defect_type/area/height_label/image_path) to
    # whatever database is actually configured above, every time the app
    # starts. Safe no-op if they already exist; never blocks startup on failure.
    from migrate_add_defect_columns import ensure_defect_columns
    ensure_defect_columns(app)

    # ── Blueprints ────────────────────────────────────────────────────────────
    from users_routes import users_bp
    app.register_blueprint(users_bp)

    from projects_routes import projects_bp
    app.register_blueprint(projects_bp)

    from chimney_routes import chimney_bp
    app.register_blueprint(chimney_bp)

    from settings_routes import settings_bp
    app.register_blueprint(settings_bp)

    from announcement_routes import announcement_bp
    app.register_blueprint(announcement_bp)

    from help_routes import help_bp
    app.register_blueprint(help_bp)

    from auth_api import auth_api_bp
    app.register_blueprint(auth_api_bp)

    from assistant_api import assistant_bp
    app.register_blueprint(assistant_bp)

    # ── Auth decorators ───────────────────────────────────────────────────────

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login'))
            return view(*args, **kwargs)
        return wrapped

    def module_required(module_name):
        def decorator(view):
            @wraps(view)
            def wrapped(*args, **kwargs):
                if 'user_id' not in session:
                    return redirect(url_for('login'))
                if session.get('role') == 'Admin':
                    return view(*args, **kwargs)
                if module_name not in session.get('modules', []):
                    return render_template('home.html',
                        error="You don't have access to this module."), 403
                return view(*args, **kwargs)
            return wrapped
        return decorator

    # Make decorators available inside route definitions below
    app.login_required  = login_required
    app.module_required = module_required

    # ── Routes ────────────────────────────────────────────────────────────────
    import users as user_store

    @app.route('/uploads/<path:filename>')
    def serve_upload(filename):
        # Uploaded files (3D tilesets, defect photos, rectification photos,
        # announcement images, PDFs) never change once created — a fresh
        # upload always gets a brand new filename/path. So unlike the app's
        # own /static/js and /static/css (which DO change on every
        # deployment and must stay uncached), these are safe to cache in
        # the browser for a full year. This is what makes reopening a
        # project feel instant instead of re-downloading the whole 3D
        # model's tile files from the network every single time.
        from flask import send_from_directory
        uploads_root = os.path.join(app.static_folder, 'uploads')
        return send_from_directory(uploads_root, filename, max_age=31536000)

    @app.route('/')
    def home():
        return render_template('home.html')

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'GET':
            return render_template('login.html')

        email    = request.form.get('email', '')
        password = request.form.get('password', '')
        login_as = request.form.get('login_as', 'Client User')

        user, error = user_store.authenticate(email, password, login_as=login_as)
        if not user:
            return render_template('login.html', error=error, login_as=login_as), 401

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

        if login_as == 'Admin':
            return redirect(url_for('admin'))
        if login_as == 'Pilot':
            return redirect(url_for('pilot_dashboard'))
        return redirect(url_for('client_dashboard'))

    @app.route('/logout')
    def logout():
        # Log how long this session lasted before clearing it, so the
        # Activity Log can show a "worked for" duration per session.
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
        return redirect(url_for('home'))

    @app.route('/switch-to-admin')
    @login_required
    def switch_to_admin():
        if 'Admin' not in session.get('all_roles', []):
            return redirect(url_for('client_dashboard'))
        session['role'] = 'Admin'
        return redirect(url_for('admin'))

    @app.route('/switch-to-client')
    @login_required
    def switch_to_client():
        if 'Client User' not in session.get('all_roles', []):
            return redirect(url_for('admin'))
        session['role'] = 'Client User'
        return redirect(url_for('client_dashboard'))

    @app.route('/admin')
    @login_required
    def admin():
        if session.get('role') != 'Admin':
            return redirect(url_for('client_dashboard'))
        can_switch_client = 'Client User' in session.get('all_roles', [])
        return render_template('admin.html',
            user_name=session['user_name'],
            can_switch_client=can_switch_client)

    @app.route('/dashboard')
    @login_required
    def client_dashboard():
        if session.get('role') == 'Admin':
            return redirect(url_for('admin'))
        assigned = session.get('modules', [])
        cards = []
        for m in assigned:
            route = user_store.MODULE_ROUTES.get(m)
            if not route:
                continue
            meta = user_store.MODULE_CARDS.get(m, {})
            cards.append({
                'name': m,
                'route': route,
                'image': meta.get('image'),
                'accent': meta.get('accent', '#b7caf1'),
                'desc': meta.get('desc'),
            })
        can_switch_admin = 'Admin' in session.get('all_roles', [])
        return render_template('client_dashboard.html',
            user_name=session['user_name'],
            user_email=session.get('user_email', ''),
            role=session.get('role', ''),
            all_roles=session.get('all_roles', []),
            modules=cards,
            can_switch_admin=can_switch_admin)

    @app.route('/pilot')
    @login_required
    def pilot_dashboard():
        if session.get('role') != 'Pilot':
            return redirect(url_for('client_dashboard'))
        return render_template('pilot_dashboard.html',
            user_name=session['user_name'])

    @app.route('/pilot/lines/<int:line_id>')
    @login_required
    def pilot_line_work(line_id):
        if session.get('role') != 'Pilot':
            return redirect(url_for('client_dashboard'))
        from models import db, Line, PilotAssignment
        line = Line.query.get_or_404(line_id)
        assignment = PilotAssignment.query.filter_by(line_id=line_id, pilot_user_id=session.get('user_id')).first()
        if not assignment:
            return "This line isn't assigned to you.", 403
        return render_template('pilot_line_work.html',
            user_name=session['user_name'], line=line)

    @app.route('/projects')
    @module_required('Transmission Line')
    def projects():
        ActivityLog.log(action='enter_module', entity_type='Module', entity_name='Transmission Line',
                         module='Transmission Line', performed_by=session.get('user_name', ''), role=session.get('role', ''))
        db.session.commit()
        return render_template('projects.html', user_name=session['user_name'], module='Transmission Line')

    @app.route('/trans')
    @module_required('TRANS')
    def trans_dashboard():
        ActivityLog.log(action='enter_module', entity_type='Module', entity_name='TRANS',
                         module='TRANS', performed_by=session.get('user_name', ''), role=session.get('role', ''))
        db.session.commit()
        return render_template('projects.html', user_name=session['user_name'], module='TRANS')

    @app.route('/dvc')
    @module_required('Land Survey')
    def dvc():
        return render_template('dvc.html', user_name=session['user_name'])

    @app.route('/mpptcl')
    @module_required('3D Inspection')
    def mpptcl():
        # Old placeholder — redirect to the real 3D Inspection dashboard.
        return redirect(url_for('chimney_bp.chimney_dashboard'))

    # ── Land Survey module ───────────────────────────────────────────────────
    # Land Survey -> Kothegudam project -> satellite base map

    @app.route('/land-survey')
    @module_required('Land Survey')
    def land_survey_dashboard():
        ActivityLog.log(action='enter_module', entity_type='Module', entity_name='Land Survey',
                         module='Land Survey', performed_by=session.get('user_name', ''), role=session.get('role', ''))
        db.session.commit()
        return render_template('land_survey_dashboard.html', user_name=session['user_name'], module='Land Survey')

    @app.route('/land-survey/kothegudam')
    @module_required('Land Survey')
    def land_survey_kothegudam():
        return render_template('kothegudam.html', user_name=session['user_name'])

    @app.route('/api/towers')
    @login_required
    def api_towers():
        with open('static/data/towers.json', 'r') as f:
            data = json.load(f)
        return jsonify(data)

    return app


# ── Entry point ───────────────────────────────────────────────────────────────
app = create_app()

if __name__ == '__main__':
    app.run(debug=True, port=5000)
