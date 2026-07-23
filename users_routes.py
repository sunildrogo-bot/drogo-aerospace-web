"""
users_routes.py — Blueprint for all /users/* routes.
Register in app.py with: app.register_blueprint(users_bp)
"""
import os
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, current_app
from werkzeug.utils import secure_filename
import users as user_store
import mailer

users_bp = Blueprint('users', __name__)

PHOTO_EXTS = {'jpg', 'jpeg', 'png', 'webp'}


def _save_user_photo(file_storage):
    """Save an uploaded profile photo under static/uploads/user_photos/
    and return the path relative to /static, '' if no file was given, or
    None if the file type isn't allowed (caller should treat that as a
    validation error)."""
    if not file_storage or not file_storage.filename:
        return ''
    ext = file_storage.filename.rsplit('.', 1)[-1].lower() if '.' in file_storage.filename else ''
    if ext not in PHOTO_EXTS:
        return None
    base_dir = current_app.root_path if hasattr(current_app, 'root_path') else '.'
    folder_fs = os.path.join(base_dir, 'static', 'uploads', 'user_photos')
    os.makedirs(folder_fs, exist_ok=True)
    safe_name = secure_filename(file_storage.filename)
    name_root, name_ext = os.path.splitext(safe_name)
    final_name = safe_name
    i = 1
    while os.path.exists(os.path.join(folder_fs, final_name)):
        final_name = f"{name_root}_{i}{name_ext}"
        i += 1
    file_storage.save(os.path.join(folder_fs, final_name))
    return f"uploads/user_photos/{final_name}"


def _require_admin():
    """Return a redirect/response if user is not a logged-in Admin, else None."""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if session.get('role') != 'Admin':
        return jsonify({'error': 'Admin access required.'}), 403
    return None


# ── Page ─────────────────────────────────────────────────────────────────────

@users_bp.route('/users')
def users_page():
    guard = _require_admin()
    if guard:
        return guard
    return render_template(
        'users.html',
        user_name=session['user_name'],
        roles=user_store.ROLES,
        modules=user_store.MODULES,
        statuses=user_store.STATUSES,
    )


# ── REST API ──────────────────────────────────────────────────────────────────

@users_bp.route('/api/users', methods=['GET'])
def api_list():
    guard = _require_admin()
    if guard:
        return guard

    users = user_store.get_all(
        search=request.args.get('search', ''),
        role=request.args.get('role', ''),
        module=request.args.get('module', ''),
        status=request.args.get('status', ''),
    )
    return jsonify({'users': users, 'total': len(users)})


@users_bp.route('/api/users/<uid>', methods=['GET'])
def api_get(uid):
    guard = _require_admin()
    if guard:
        return guard

    user = user_store.get_by_id(uid)
    if not user:
        return jsonify({'error': 'Not found'}), 404
    return jsonify(user)


@users_bp.route('/api/users/projects-for-module', methods=['GET'])
def api_projects_for_module():
    """Which projects exist in a given module — populates the "restrict to
    specific projects" checkbox list in the Add/Edit User form. Handles
    both of this app's two different "project" concepts: the generic
    Project model (Transmission Line / Land Survey / TRANS) and
    ChimneyProject (3D Inspection), which is a separate table entirely."""
    guard = _require_admin()
    if guard:
        return guard
    module = (request.args.get('module') or '').strip()
    if not module:
        return jsonify({'projects': []})

    from models import Project, ChimneyProject
    if module == '3D Inspection':
        rows = ChimneyProject.query.order_by(ChimneyProject.asset_name.asc()).all()
        return jsonify({'projects': [{'id': p.id, 'name': p.asset_name} for p in rows]})

    rows = Project.query.filter_by(module=module).order_by(Project.name.asc()).all()
    return jsonify({'projects': [{'id': p.id, 'name': p.name} for p in rows]})


@users_bp.route('/api/users', methods=['POST'])
def api_create():
    guard = _require_admin()
    if guard:
        return guard

    # multipart/form-data, not JSON — needed to carry the photo file
    # alongside the rest of the fields. roles/modules arrive as repeated
    # form keys (roles=Admin&roles=Client User), read with getlist.
    data = {
        'username': request.form.get('username', ''),
        'email':    request.form.get('email', ''),
        'contact':  request.form.get('contact', ''),
        'status':   request.form.get('status', ''),
        'roles':    request.form.getlist('roles'),
        'modules':  request.form.getlist('modules'),
        'project_ids':          request.form.getlist('project_ids'),
        'chimney_project_ids':  request.form.getlist('chimney_project_ids'),
    }
    errors = _validate(data)
    if errors:
        return jsonify({'error': errors}), 400

    photo_file = request.files.get('photo')
    if photo_file and photo_file.filename:
        saved = _save_user_photo(photo_file)
        if saved is None:
            return jsonify({'error': 'Profile photo must be a .jpg, .png, or .webp file.'}), 400
        data['photo_path'] = saved

    new_user = user_store.create(data)

    # Email the system-generated password to the new user (Gmail SMTP).
    # This never blocks user creation — if mail isn't configured or the
    # send fails, we still return 201 but flag _email_sent = False so the
    # admin UI can fall back to showing the password directly.
    try:
        login_url = url_for('login', _external=True)
    except Exception:
        login_url = ''

    email_sent = mailer.send_welcome_email(
        to_email=new_user['email'],
        username=new_user['username'],
        password=new_user.get('_generated_password', ''),
        login_url=login_url,
    )
    new_user['_email_sent'] = email_sent

    return jsonify(new_user), 201


@users_bp.route('/api/users/<uid>', methods=['PUT'])
def api_update(uid):
    guard = _require_admin()
    if guard:
        return guard

    data = {
        'username': request.form.get('username', ''),
        'email':    request.form.get('email', ''),
        'contact':  request.form.get('contact', ''),
        'status':   request.form.get('status', ''),
        'roles':    request.form.getlist('roles'),
        'modules':  request.form.getlist('modules'),
        'project_ids':          request.form.getlist('project_ids'),
        'chimney_project_ids':  request.form.getlist('chimney_project_ids'),
    }
    errors = _validate(data)
    if errors:
        return jsonify({'error': errors}), 400

    photo_file = request.files.get('photo')
    if photo_file and photo_file.filename:
        saved = _save_user_photo(photo_file)
        if saved is None:
            return jsonify({'error': 'Profile photo must be a .jpg, .png, or .webp file.'}), 400
        data['photo_path'] = saved

    updated = user_store.update(uid, data)
    if not updated:
        return jsonify({'error': 'Not found'}), 404

    # Keep this admin's own session in sync if they edit their own roles/modules
    if uid == session.get('user_id'):
        session['all_roles'] = updated['roles']
        session['modules']   = updated['modules']
        if session.get('role') not in updated['roles']:
            session['role'] = updated['roles'][0] if updated['roles'] else 'Client User'

    return jsonify(updated)


@users_bp.route('/api/users/<uid>', methods=['DELETE'])
def api_delete(uid):
    guard = _require_admin()
    if guard:
        return guard

    ok = user_store.delete(uid)
    if not ok:
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'deleted': uid})


# ── Validation ────────────────────────────────────────────────────────────────

def _validate(data: dict) -> str:
    if not data.get('username', '').strip():
        return 'Username is required.'
    if not data.get('email', '').strip():
        return 'Email is required.'
    if '@' not in data.get('email', ''):
        return 'Invalid email address.'
    roles = data.get('roles') or []
    if not isinstance(roles, list) or not roles:
        return 'At least one role must be selected.'
    invalid_roles = [r for r in roles if r not in user_store.ROLES]
    if invalid_roles:
        return f"Invalid role(s): {', '.join(invalid_roles)}"
    modules = data.get('modules') or []
    if not isinstance(modules, list) or not modules:
        return 'At least one module must be selected.'
    invalid = [m for m in modules if m not in user_store.MODULES]
    if invalid:
        return f"Invalid module(s): {', '.join(invalid)}"
    return ''
