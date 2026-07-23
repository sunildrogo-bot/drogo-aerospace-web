"""
chimney_routes.py — Blueprint for the Chimney Inspection module ONLY.

Self-contained: uses its own ChimneyProject / ChimneyDefect models and its
own /static/uploads/chimney_tiles/ folder. Does not touch the shared
Project / Division / Line tables or routes used by Transmission Line /
Land Survey.

    ChimneyProject  (+ Add Chimney: asset name, inspection type, structure
                      type, lat/long)
        ├── tileset upload (.3tz / .zip containing tileset.json) → 3D Tiles
        └── ChimneyDefect  (pins dropped on the 3D model during inspection)

Register in app.py with: app.register_blueprint(chimney_bp)
"""
import os
import json
import zipfile
import shutil
import tempfile
from datetime import datetime

from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, current_app
from werkzeug.utils import secure_filename

from models import db, ChimneyProject, ChimneyDefect, ChimneyProjectAccess, ActivityLog, User
import settings as app_settings

chimney_bp = Blueprint('chimney_bp', __name__)

TILESET_EXTS = {'3tz', 'zip'}
TILES_SUBDIR = os.path.join('uploads', 'chimney_tiles')   # under /static


def _login_guard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return None


def _admin_guard():
    """Blocks write actions for anyone whose active session role isn't
    Admin. Client User sessions get a 403 — the chimney module is
    view + measure only for that role; all editing (findings, tileset,
    KML, project fields) stays Admin-only."""
    guard = _login_guard()
    if guard:
        return guard
    if session.get('role') != 'Admin':
        return jsonify({'error': 'Your account has view-only access to this module.'}), 403
    return None


def _visible_chimney_project_ids():
    """None means "no restriction, show every project" — Admin sessions
    always get this, and so does a Client User who hasn't had any
    specific projects assigned (the backward-compatible default)."""
    if session.get('role') == 'Admin':
        return None
    user = User.query.get(session.get('user_id'))
    if not user:
        return set()
    return user.restricted_chimney_project_ids()


def _chimney_project_access_guard(project):
    """For direct-URL access to a specific project's pages — the list
    endpoint filtering only helps if someone goes through the list; this
    closes the gap for a direct/guessed link to a project a Client User
    isn't allowed to see. Returns a Flask response to abort with, or None
    if access is OK."""
    allowed_ids = _visible_chimney_project_ids()
    if allowed_ids is not None and project.id not in allowed_ids:
        return jsonify({'error': "You don't have access to this project."}), 403
    return None


def _ext_ok(filename, allowed):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed


def _find_tileset_json(root_dir):
    """Walk an extracted archive and return the path to tileset.json, preferring
    the shallowest match (in case the archive has a single wrapper folder)."""
    candidates = []
    for dirpath, _dirnames, filenames in os.walk(root_dir):
        for f in filenames:
            if f.lower() == 'tileset.json':
                candidates.append(os.path.join(dirpath, f))
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.count(os.sep))
    return candidates[0]


# ── Page routes ───────────────────────────────────────────────────────────────

@chimney_bp.route('/chimney-inspection')
def chimney_dashboard():
    guard = _login_guard()
    if guard:
        return guard
    ActivityLog.log(action='enter_module', entity_type='Module', entity_name='3D Inspection',
                     module='3D Inspection', performed_by=session.get('user_name', ''), role=session.get('role', ''))
    db.session.commit()
    return render_template('chimney_dashboard.html', user_name=session.get('user_name', ''),
                            is_admin=session.get('role') == 'Admin')


@chimney_bp.route('/chimney-projects/<int:chimney_id>')
def chimney_viewer(chimney_id):
    guard = _login_guard()
    if guard:
        return guard
    project = ChimneyProject.query.get_or_404(chimney_id)
    guard = _chimney_project_access_guard(project)
    if guard:
        return guard
    is_admin = session.get('role') == 'Admin'
    ChimneyProjectAccess.record(chimney_id, session.get('user_name', ''), session.get('role', ''))
    ActivityLog.log(action='enter_project', entity_type='ChimneyProject', entity_name=project.asset_name,
                     module='3D Inspection', performed_by=session.get('user_name', ''), role=session.get('role', ''))
    db.session.commit()

    # /uploads/... (long-cached, see app.py) instead of /static/... (never
    # cached) — the tileset never changes once uploaded, so this is what
    # lets reopening a project load instantly from the browser cache
    # instead of re-streaming every tile file over the network again.
    tileset_url = None
    if project.tileset_path:
        rel = project.tileset_path[len('uploads/'):] if project.tileset_path.startswith('uploads/') else project.tileset_path
        tileset_url = url_for('serve_upload', filename=rel)

    cover_snapshot_url = None
    if project.cover_snapshot_path:
        rel = project.cover_snapshot_path[len('uploads/'):] if project.cover_snapshot_path.startswith('uploads/') else project.cover_snapshot_path
        cover_snapshot_url = url_for('serve_upload', filename=rel)

    return render_template('chimney_project.html', project=project, user_name=session.get('user_name', ''),
                            is_admin=is_admin, tileset_url=tileset_url, cover_snapshot_url=cover_snapshot_url)


@chimney_bp.route('/chimney-projects/<int:chimney_id>/detail')
def chimney_detail(chimney_id):
    guard = _login_guard()
    if guard:
        return guard
    project = ChimneyProject.query.get_or_404(chimney_id)
    guard = _chimney_project_access_guard(project)
    if guard:
        return guard
    is_admin = session.get('role') == 'Admin'
    ChimneyProjectAccess.record(chimney_id, session.get('user_name', ''), session.get('role', ''))

    defects = sorted(project.defects, key=lambda d: d.created_at or datetime.min)
    # Sequence numbers ("D1", "D2", ...) computed from the FULL list's
    # created-at order, matching exactly what the 3D viewer's observation
    # panel shows for each defect — done here, before splitting into
    # open/closed, so the numbering stays consistent between the two
    # views regardless of which subset a given defect ends up in.
    seq_by_id = {d.id: i + 1 for i, d in enumerate(defects)}
    open_defects = [d for d in defects if (d.status or 'Open') == 'Open']
    closed_defects = [d for d in defects if (d.status or 'Open') == 'Closed']
    access_log = ChimneyProjectAccess.query.filter_by(chimney_project_id=chimney_id) \
        .order_by(ChimneyProjectAccess.last_accessed_at.desc()).all()

    # Severity breakdown for the bar graph — counts across every finding
    # (open + closed combined), since clicking a bar filters both lists.
    sev_counts = {'Minor': 0, 'Moderate': 0, 'Critical': 0}
    for d in defects:
        sev = d.severity if d.severity in sev_counts else 'Minor'
        sev_counts[sev] += 1
    total_count = len(defects)
    tallest = max([*sev_counts.values(), total_count, 1])
    severity_bars = [
        {'label': 'Minor', 'count': sev_counts['Minor'], 'pct': round(sev_counts['Minor'] / tallest * 100), 'color': 'var(--success)'},
        {'label': 'Moderate', 'count': sev_counts['Moderate'], 'pct': round(sev_counts['Moderate'] / tallest * 100), 'color': 'var(--warning)'},
        {'label': 'Critical', 'count': sev_counts['Critical'], 'pct': round(sev_counts['Critical'] / tallest * 100), 'color': 'var(--danger)'},
        {'label': 'Total', 'count': total_count, 'pct': round(total_count / tallest * 100), 'color': 'var(--accent)'},
    ]

    # STATUS PIE — Open vs Closed, real wedges (conic-gradient). This is
    # deliberately a different dimension from the severity bar chart next
    # to it (status vs severity) so the two visuals aren't redundant —
    # this one is what visibly moves as findings get closed out.
    open_count, closed_count = len(open_defects), len(closed_defects)
    if total_count:
        open_deg = round(open_count / total_count * 360, 2)
        status_pie_gradient = f'conic-gradient(var(--danger) 0deg {open_deg}deg, var(--success) {open_deg}deg 360deg)'
    else:
        status_pie_gradient = 'conic-gradient(var(--border) 0deg 360deg)'

    # Small extra insight: average time from a finding being raised to
    # being closed, across whichever findings actually have both dates.
    close_durations = [
        (d.closed_at.date() - d.created_at.date()).days
        for d in closed_defects if d.closed_at and d.created_at
    ]
    avg_days_to_close = round(sum(close_durations) / len(close_durations), 1) if close_durations else None

    # Another cut of the same data: which kind of defect shows up most
    # (crack, spalling, corrosion, etc.) — nothing else on this page
    # surfaces defect_type at all.
    type_counts = {}
    for d in defects:
        t = (d.defect_type or 'Unclassified').strip() or 'Unclassified'
        type_counts[t] = type_counts.get(t, 0) + 1
    top_defect_types = sorted(type_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]

    return render_template('chimney_detail.html',
        project=project,
        user_name=session.get('user_name', ''),
        is_admin=is_admin,
        open_defects=open_defects,
        closed_defects=closed_defects,
        seq_by_id=seq_by_id,
        summary=project.completion_summary(),
        access_log=access_log,
        severity_bars=severity_bars,
        status_pie_gradient=status_pie_gradient,
        avg_days_to_close=avg_days_to_close,
        top_defect_types=top_defect_types,
    )


# ── Chimney project CRUD ───────────────────────────────────────────────────────

@chimney_bp.route('/api/chimney-projects', methods=['GET'])
def api_list_chimney_projects():
    guard = _login_guard()
    if guard:
        return guard
    projects = ChimneyProject.query.order_by(ChimneyProject.created_at.desc()).all()
    allowed_ids = _visible_chimney_project_ids()
    if allowed_ids is not None:
        projects = [p for p in projects if p.id in allowed_ids]
    return jsonify({'projects': [p.to_dict() for p in projects]})


@chimney_bp.route('/api/chimney-projects', methods=['POST'])
def api_create_chimney_project():
    guard = _admin_guard()
    if guard:
        return guard

    form = request.form
    asset_name = (form.get('asset_name') or '').strip()
    if not asset_name:
        return jsonify({'error': 'Asset name is required.'}), 400

    try:
        lat = float(form.get('latitude'))
        lng = float(form.get('longitude'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Latitude and longitude must be valid numbers.'}), 400
    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        return jsonify({'error': 'Latitude/longitude out of range.'}), 400

    target_date = None
    raw_target = (form.get('target_completion_date') or '').strip()
    if raw_target:
        try:
            target_date = datetime.strptime(raw_target, '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'error': 'Timeline date must be a valid date.'}), 400

    asset_category = (form.get('asset_category') or 'chimney').strip().lower()
    if asset_category not in ('chimney', 'water_tank'):
        asset_category = 'chimney'

    project = ChimneyProject(
        asset_category=asset_category,
        asset_name=asset_name,
        inspection_type=(form.get('inspection_type') or '').strip(),
        structure_type=(form.get('structure_type') or '').strip(),
        inspection_scope=(form.get('inspection_scope') or '').strip(),
        pilots=(form.get('pilots') or '').strip(),
        target_completion_date=target_date,
        latitude=lat,
        longitude=lng,
        created_by=session.get('user_id'),
    )
    db.session.add(project)
    db.session.commit()
    return jsonify(project.to_dict()), 201


@chimney_bp.route('/api/chimney-projects/<int:chimney_id>', methods=['GET'])
def api_get_chimney_project(chimney_id):
    guard = _login_guard()
    if guard:
        return guard
    project = ChimneyProject.query.get_or_404(chimney_id)
    data = project.to_dict()
    data['defects'] = [d.to_dict() for d in project.defects]
    return jsonify(data)


@chimney_bp.route('/api/chimney-projects/<int:chimney_id>', methods=['PATCH'])
def api_update_chimney_project(chimney_id):
    guard = _admin_guard()
    if guard:
        return guard
    project = ChimneyProject.query.get_or_404(chimney_id)
    data = request.get_json(force=True, silent=True) or request.form

    if 'asset_name' in data:
        name = (data.get('asset_name') or '').strip()
        if not name:
            return jsonify({'error': 'Asset name cannot be empty.'}), 400
        project.asset_name = name
    if 'inspection_type' in data:
        project.inspection_type = (data.get('inspection_type') or '').strip()
    if 'structure_type' in data:
        project.structure_type = (data.get('structure_type') or '').strip()
    if 'inspection_scope' in data:
        project.inspection_scope = (data.get('inspection_scope') or '').strip()
    if 'pilots' in data:
        project.pilots = (data.get('pilots') or '').strip()
    if 'target_completion_date' in data:
        raw_target = (data.get('target_completion_date') or '').strip()
        if raw_target:
            try:
                project.target_completion_date = datetime.strptime(raw_target, '%Y-%m-%d').date()
            except ValueError:
                return jsonify({'error': 'Timeline date must be a valid date.'}), 400
        else:
            project.target_completion_date = None

    db.session.commit()
    return jsonify(project.to_dict())


@chimney_bp.route('/api/chimney-projects/<int:chimney_id>', methods=['DELETE'])
def api_delete_chimney_project(chimney_id):
    guard = _admin_guard()
    if guard:
        return guard
    project = ChimneyProject.query.get_or_404(chimney_id)

    data = request.get_json(force=True, silent=True) or {}
    password = (data.get('password') or request.args.get('password') or '').strip()
    if not app_settings.verify_delete_password(password):
        return jsonify({'error': 'Incorrect delete password.'}), 403

    asset_name = project.asset_name

    if project.tileset_path:
        tiles_dir = os.path.join(current_app.root_path, 'static', TILES_SUBDIR, str(project.id))
        shutil.rmtree(tiles_dir, ignore_errors=True)

    db.session.delete(project)
    ActivityLog.log(action='delete', entity_type='ChimneyProject', entity_name=asset_name,
                     module='Chimney Inspection', performed_by=session.get('user_name', ''))
    db.session.commit()
    return jsonify({'deleted': chimney_id})


# ── 3D tileset upload (.3tz / .zip) ───────────────────────────────────────────

@chimney_bp.route('/api/chimney-projects/<int:chimney_id>/tileset', methods=['POST'])
def api_upload_tileset(chimney_id):
    guard = _admin_guard()
    if guard:
        return guard
    project = ChimneyProject.query.get_or_404(chimney_id)

    file = request.files.get('tileset_file')
    if not file or not file.filename:
        return jsonify({'error': 'No file uploaded.'}), 400
    if not _ext_ok(file.filename, TILESET_EXTS):
        return jsonify({'error': 'File must be a .3tz or .zip archive containing tileset.json.'}), 400

    dest_dir = os.path.join(current_app.root_path, 'static', TILES_SUBDIR, str(project.id))
    # Wipe any previous model for this chimney before extracting the new one.
    shutil.rmtree(dest_dir, ignore_errors=True)
    os.makedirs(dest_dir, exist_ok=True)

    with tempfile.NamedTemporaryFile(suffix=secure_filename(file.filename), delete=False) as tmp:
        tmp_path = tmp.name
        file.save(tmp_path)

    try:
        if not zipfile.is_zipfile(tmp_path):
            return jsonify({'error': 'That file is not a valid .3tz/.zip archive.'}), 400
        with zipfile.ZipFile(tmp_path) as zf:
            zf.extractall(dest_dir)
    except zipfile.BadZipFile:
        return jsonify({'error': 'Could not read the archive — it may be corrupted.'}), 400
    finally:
        os.remove(tmp_path)

    tileset_json = _find_tileset_json(dest_dir)
    if not tileset_json:
        shutil.rmtree(dest_dir, ignore_errors=True)
        return jsonify({'error': 'No tileset.json found inside that archive.'}), 400

    rel_path = os.path.relpath(tileset_json, os.path.join(current_app.root_path, 'static'))
    project.tileset_path = rel_path.replace(os.sep, '/')
    db.session.commit()

    return jsonify(project.to_dict()), 201


@chimney_bp.route('/api/chimney-projects/<int:chimney_id>/model-center', methods=['POST'])
def api_set_model_center(chimney_id):
    """Called once by the client right after the 3D tileset finishes loading.
    Saves the tileset's true geographic centre (its bounding-sphere centre,
    converted to lon/lat) so defect compass directions can be measured from
    where the model actually sits, instead of the manually-entered project
    lat/lon — which is what caused Direction to be wrong/inconsistent."""
    guard = _admin_guard()
    if guard:
        return guard
    project = ChimneyProject.query.get_or_404(chimney_id)
    data = request.get_json(force=True, silent=True) or {}
    try:
        lat = float(data.get('lat'))
        lng = float(data.get('lng'))
    except (TypeError, ValueError):
        return jsonify({'error': 'lat and lng are required numbers.'}), 400
    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        return jsonify({'error': 'lat/lng out of range.'}), 400

    project.model_center_lat = lat
    project.model_center_lng = lng
    db.session.commit()
    return jsonify(project.to_dict())


@chimney_bp.route('/api/chimney-projects/<int:chimney_id>/structure-height', methods=['POST'])
def api_set_structure_height(chimney_id):
    """Called once by the client right after the 3D tileset finishes
    loading. Saves the structure's overall height in meters, calculated
    from the model's real geometric extent (top of the model minus the
    detected ground level) — shown automatically on the Project Timeline
    card, no manual entry needed."""
    guard = _admin_guard()
    if guard:
        return guard
    project = ChimneyProject.query.get_or_404(chimney_id)
    data = request.get_json(force=True, silent=True) or {}
    try:
        height_m = float(data.get('height_m'))
    except (TypeError, ValueError):
        return jsonify({'error': 'height_m is required and must be a number.'}), 400
    if not (0 < height_m < 2000):
        return jsonify({'error': 'height_m out of plausible range.'}), 400

    project.structure_height_m = height_m
    db.session.commit()
    return jsonify(project.to_dict())


@chimney_bp.route('/api/chimney-projects/<int:chimney_id>/cover-snapshot', methods=['POST'])
def api_save_cover_snapshot(chimney_id):
    """Saves a JPEG snapshot of the fully-loaded model, captured client-side
    the first time an Admin session finishes loading it. Shown as an instant
    placeholder image on future visits — see cover_snapshot_path on
    ChimneyProject. Only ever captured once per project; the client skips
    calling this at all if a snapshot already exists."""
    guard = _admin_guard()
    if guard:
        return guard
    project = ChimneyProject.query.get_or_404(chimney_id)

    file = request.files.get('snapshot')
    if not file or not file.filename:
        return jsonify({'error': 'No snapshot file uploaded.'}), 400

    dest_dir = os.path.join(current_app.root_path, 'static', 'uploads', 'chimney_covers')
    os.makedirs(dest_dir, exist_ok=True)
    filename = f'cover_{project.id}.jpg'
    dest_path = os.path.join(dest_dir, filename)
    file.save(dest_path)

    rel_path = os.path.relpath(dest_path, os.path.join(current_app.root_path, 'static'))
    project.cover_snapshot_path = rel_path.replace(os.sep, '/')
    db.session.commit()
    return jsonify({'saved': True}), 201


# ── Defect pins (inspection tool) ─────────────────────────────────────────────

@chimney_bp.route('/api/chimney-projects/<int:chimney_id>/defects', methods=['GET'])
def api_list_defects(chimney_id):
    guard = _login_guard()
    if guard:
        return guard
    ChimneyProject.query.get_or_404(chimney_id)
    defects = ChimneyDefect.query.filter_by(chimney_project_id=chimney_id).order_by(ChimneyDefect.created_at.asc()).all()
    return jsonify({'defects': [d.to_dict() for d in defects]})


@chimney_bp.route('/api/chimney-projects/<int:chimney_id>/defects', methods=['POST'])
def api_create_defect(chimney_id):
    guard = _admin_guard()
    if guard:
        return guard
    chimney_project = ChimneyProject.query.get_or_404(chimney_id)

    data = request.get_json(force=True, silent=True) or request.form
    title = (data.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'Defect title is required.'}), 400

    severity = (data.get('severity') or 'Minor').strip()
    if severity not in ('Minor', 'Moderate', 'Critical'):
        severity = 'Minor'

    status = (data.get('status') or 'Open').strip()
    if status not in ('Open', 'Closed'):
        status = 'Open'

    try:
        pos = data.get('position') or {}
        lon = float(pos.get('lon'))
        lat = float(pos.get('lat'))
        height = float(pos.get('height') or 0)
    except (TypeError, ValueError, AttributeError):
        return jsonify({'error': 'A valid pin position (lon, lat, height) is required.'}), 400

    shape_type = data.get('shape_type') or None
    if shape_type not in ('line', 'rect', 'polygon', 'circle', None):
        shape_type = None
    shape_coords_in = data.get('shape_coords')
    shape_coords_json = None
    if shape_type and isinstance(shape_coords_in, list) and shape_coords_in:
        try:
            clean = [{'lon': float(p['lon']), 'lat': float(p['lat']), 'height': float(p.get('height') or 0)}
                      for p in shape_coords_in]
            shape_coords_json = json.dumps(clean)
        except (TypeError, ValueError, KeyError):
            shape_type = None  # malformed shape data — fall back to a plain pin rather than failing the request

    # The already-computed, surface-hugging outline (see the model's
    # rendered_positions field for why this is stored rather than
    # re-derived from shape_coords on every future load).
    rendered_json = None
    rendered_in = data.get('rendered_positions')
    if shape_type and isinstance(rendered_in, list) and len(rendered_in) >= 2:
        try:
            rendered_json = json.dumps([
                {'lon': float(p['lon']), 'lat': float(p['lat']), 'height': float(p.get('height') or 0)}
                for p in rendered_in
            ])
        except (TypeError, ValueError, KeyError):
            rendered_json = None

    defect = ChimneyDefect(
        chimney_project_id=chimney_id,
        title=title,
        severity=severity,
        status=status,
        notes=(data.get('notes') or '').strip(),
        defect_type=(data.get('defect_type') or '').strip(),
        area=(data.get('area') or '').strip(),
        height_label=(data.get('height') or '').strip(),
        location=(data.get('location') or '').strip(),
        pos_lon=lon, pos_lat=lat, pos_height=height,
        shape_type=shape_type,
        shape_coords=shape_coords_json,
        rendered_positions=rendered_json,
    )
    db.session.add(defect)
    ActivityLog.log(action='mark_defect', entity_type='ChimneyDefect',
                     entity_name=f"{title} — {chimney_project.asset_name}",
                     module='3D Inspection', performed_by=session.get('user_name', ''), role=session.get('role', ''))
    db.session.commit()
    return jsonify(defect.to_dict()), 201


@chimney_bp.route('/api/chimney-defects/<int:defect_id>', methods=['PUT'])
def api_update_defect(defect_id):
    guard = _admin_guard()
    if guard:
        return guard
    defect = ChimneyDefect.query.get_or_404(defect_id)

    data = request.get_json(force=True, silent=True) or request.form

    title = (data.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'Defect title is required.'}), 400

    severity = (data.get('severity') or defect.severity or 'Minor').strip()
    if severity not in ('Minor', 'Moderate', 'Critical'):
        severity = 'Minor'

    status = (data.get('status') or defect.status or 'Open').strip()
    if status not in ('Open', 'Closed'):
        status = defect.status or 'Open'

    pos = data.get('position') or {}
    try:
        if pos:
            defect.pos_lon = float(pos.get('lon'))
            defect.pos_lat = float(pos.get('lat'))
            defect.pos_height = float(pos.get('height') or 0)
    except (TypeError, ValueError):
        pass

    shape_type = data.get('shape_type') or None
    if shape_type not in ('line', 'rect', 'polygon', 'circle', None):
        shape_type = None
    shape_coords_in = data.get('shape_coords')
    if shape_type and isinstance(shape_coords_in, list) and shape_coords_in:
        try:
            clean = [{'lon': float(p['lon']), 'lat': float(p['lat']), 'height': float(p.get('height') or 0)}
                      for p in shape_coords_in]
            defect.shape_coords = json.dumps(clean)
            defect.shape_type = shape_type
        except (TypeError, ValueError, KeyError):
            pass

    # See create endpoint above / the model field's own comment for why
    # this is stored instead of re-derived on every future load. Only
    # updated when the shape itself was actually re-drawn this edit (the
    # client only sends this alongside a real shape_coords change) — an
    # edit that just changes the title/severity/notes shouldn't touch it.
    rendered_in = data.get('rendered_positions')
    if shape_type and isinstance(rendered_in, list) and len(rendered_in) >= 2:
        try:
            defect.rendered_positions = json.dumps([
                {'lon': float(p['lon']), 'lat': float(p['lat']), 'height': float(p.get('height') or 0)}
                for p in rendered_in
            ])
        except (TypeError, ValueError, KeyError):
            pass

    defect.title = title
    defect.severity = severity
    defect.status = status
    defect.notes = (data.get('notes') or '').strip()
    defect.defect_type = (data.get('defect_type') or defect.defect_type or '').strip()
    defect.area = (data.get('area') or defect.area or '').strip()
    defect.height_label = (data.get('height') or defect.height_label or '').strip()
    defect.location = (data.get('location') or defect.location or '').strip()

    db.session.commit()
    return jsonify(defect.to_dict())


@chimney_bp.route('/api/chimney-defects/<int:defect_id>/position', methods=['PATCH'])
def api_recenter_defect_pin(defect_id):
    """Explicit, user-triggered re-centering of a defect's marker/pin
    position — used to fix existing defects whose pin was placed off to
    one side by an older, less accurate centroid calculation, without
    needing to redraw the shape itself. Only ever touches pos_lat/
    pos_lon/pos_height — the shape's own outline (shape_coords) is never
    modified, since that geometry has proven reliable and stable and
    isn't what needed fixing."""
    guard = _admin_guard()
    if guard:
        return guard
    defect = ChimneyDefect.query.get_or_404(defect_id)
    data = request.get_json(force=True, silent=True) or {}
    try:
        lon = float(data['lon'])
        lat = float(data['lat'])
        height = float(data['height'])
    except (KeyError, TypeError, ValueError):
        return jsonify({'error': 'lon, lat, and height are all required and must be numbers.'}), 400

    defect.pos_lon = lon
    defect.pos_lat = lat
    defect.pos_height = height
    db.session.commit()
    return jsonify(defect.to_dict())


@chimney_bp.route('/api/chimney-defects/<int:defect_id>/height-sync', methods=['PATCH'])
def api_sync_defect_height(defect_id):
    """Lightweight, best-effort update of ONLY the stored height_label —
    called silently by the client whenever it recomputes a defect's
    height-from-ground live and finds it meaningfully differs from what
    was stored. The stored value gets frozen in at whatever moment the
    defect was created, using whatever ground-detection result the
    browser had at that instant; this keeps the server-rendered project
    overview table (which has no way to recompute it itself) in sync as
    that detection keeps improving, without requiring every old defect to
    be manually re-saved."""
    guard = _admin_guard()
    if guard:
        return guard
    defect = ChimneyDefect.query.get_or_404(defect_id)
    data = request.get_json(force=True, silent=True) or {}
    height = (data.get('height') or '').strip()
    if not height:
        return jsonify({'error': 'height is required.'}), 400
    defect.height_label = height
    db.session.commit()
    return jsonify({'ok': True})


@chimney_bp.route('/api/chimney-defects/<int:defect_id>/status', methods=['PATCH'])
def api_update_defect_status(defect_id):
    """Reopen a closed finding. Closing a finding is NOT done here — it
    requires a rectification photo, so use POST .../close instead."""
    guard = _admin_guard()
    if guard:
        return guard
    defect = ChimneyDefect.query.get_or_404(defect_id)
    data = request.get_json(force=True, silent=True) or {}
    status = (data.get('status') or '').strip()
    if status != 'Open':
        return jsonify({'error': "Only reopening is allowed here — closing a finding requires a rectification photo via POST /api/chimney-defects/<id>/close."}), 400
    defect.status = 'Open'
    defect.closed_at = None
    db.session.commit()
    return jsonify(defect.to_dict())


DEFECT_IMG_SUBDIR = os.path.join('uploads', 'chimney_defect_images')       # under /static — original finding photo
DEFECT_RECTIFIED_SUBDIR = os.path.join('uploads', 'chimney_rectified_images')  # under /static — proof-of-fix photo


@chimney_bp.route('/api/chimney-defects/<int:defect_id>/close', methods=['POST'])
def api_close_defect(defect_id):
    """Marks a finding Closed. Requires a rectification photo showing the
    fix, so there's photographic proof scoped to that specific finding
    before it moves to Closed. Both Admin and Client can do this."""
    guard = _login_guard()
    if guard:
        return guard
    defect = ChimneyDefect.query.get_or_404(defect_id)

    file = request.files.get('photo')
    if not file or not file.filename:
        return jsonify({'error': 'A rectification photo is required to close a finding.'}), 400

    dest_dir = os.path.join(current_app.root_path, 'static', DEFECT_RECTIFIED_SUBDIR, str(defect.chimney_project_id))
    os.makedirs(dest_dir, exist_ok=True)

    # Replace any previous rectification photo for this finding (e.g. it
    # was reopened and is being closed again).
    if defect.rectified_image_path:
        old_path = os.path.join(current_app.root_path, 'static', defect.rectified_image_path)
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass

    filename = f'rectified_{defect.id}.jpg'
    dest_path = os.path.join(dest_dir, filename)
    file.save(dest_path)

    rel_path = os.path.relpath(dest_path, os.path.join(current_app.root_path, 'static'))
    defect.rectified_image_path = rel_path.replace(os.sep, '/')
    defect.status = 'Closed'
    defect.closed_at = datetime.utcnow()
    defect.closed_by = session.get('user_name', '')

    notes = (request.form.get('notes') or '').strip()
    if notes:
        defect.notes = (defect.notes + '\n\n' if defect.notes else '') + f'[Rectification note] {notes}'

    db.session.commit()
    return jsonify(defect.to_dict()), 201


@chimney_bp.route('/api/chimney-defects/<int:defect_id>', methods=['DELETE'])
def api_delete_defect(defect_id):
    guard = _admin_guard()
    if guard:
        return guard
    defect = ChimneyDefect.query.get_or_404(defect_id)
    if defect.image_path:
        img_path = os.path.join(current_app.root_path, 'static', defect.image_path)
        if os.path.exists(img_path):
            try:
                os.remove(img_path)
            except OSError:
                pass
    db.session.delete(defect)
    db.session.commit()
    return jsonify({'deleted': defect_id})


# ── Defect snapshot image (saved to server, viewable + downloadable) ─────────



@chimney_bp.route('/api/chimney-defects/<int:defect_id>/image', methods=['POST'])
def api_upload_defect_image(defect_id):
    guard = _admin_guard()
    if guard:
        return guard
    defect = ChimneyDefect.query.get_or_404(defect_id)

    file = request.files.get('image')
    if not file or not file.filename:
        return jsonify({'error': 'No image uploaded.'}), 400

    dest_dir = os.path.join(current_app.root_path, 'static', DEFECT_IMG_SUBDIR, str(defect.chimney_project_id))
    os.makedirs(dest_dir, exist_ok=True)

    # Remove any previous snapshot for this defect first.
    if defect.image_path:
        old_path = os.path.join(current_app.root_path, 'static', defect.image_path)
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass

    filename = f'defect_{defect.id}.jpg'
    dest_path = os.path.join(dest_dir, filename)
    file.save(dest_path)

    rel_path = os.path.relpath(dest_path, os.path.join(current_app.root_path, 'static'))
    defect.image_path = rel_path.replace(os.sep, '/')

    # Persist the exact camera pose this capture was taken from, if the
    # client sent one — see cam_pos_x/etc. on the model for why. All nine
    # are optional/best-effort: if any are missing or invalid, this is
    # simply skipped and flyToDefect() falls back to computing the view
    # live, exactly like before this feature existed.
    pose_fields = ['cam_pos_x', 'cam_pos_y', 'cam_pos_z',
                   'cam_dir_x', 'cam_dir_y', 'cam_dir_z',
                   'cam_up_x', 'cam_up_y', 'cam_up_z']
    try:
        pose_values = {f: float(request.form[f]) for f in pose_fields if f in request.form}
        if len(pose_values) == len(pose_fields):
            for f, v in pose_values.items():
                setattr(defect, f, v)
    except (TypeError, ValueError):
        pass  # malformed pose data — keep the image, just skip the pose

    db.session.commit()

    return jsonify({'image_url': f'/static/{defect.image_path}'}), 201


@chimney_bp.route('/api/chimney-defects/<int:defect_id>/image/download', methods=['GET'])
def api_download_defect_image(defect_id):
    from flask import send_file
    guard = _login_guard()
    if guard:
        return guard
    defect = ChimneyDefect.query.get_or_404(defect_id)
    if not defect.image_path:
        return jsonify({'error': 'No saved image for this finding yet.'}), 404
    full_path = os.path.join(current_app.root_path, 'static', defect.image_path)
    if not os.path.exists(full_path):
        return jsonify({'error': 'Saved image file is missing on the server.'}), 404
    safe_title = ''.join(c for c in (defect.title or 'defect') if c.isalnum() or c in ' _-').strip().replace(' ', '_')[:40]
    download_name = f'defect_{defect.id}_{safe_title or "image"}.jpg'
    return send_file(full_path, as_attachment=True, download_name=download_name, mimetype='image/jpeg')


# ── KML defect overlay upload ──────────────────────────────────────────────────
# NOTE: KML is parsed client-side by Cesium's KmlDataSource, so we only need
# this endpoint if you want server-side persistence.  The frontend can also
# load KML directly from a blob URL without hitting the server at all — that's
# the default path used by uploadKml() in chimney_viewer.js.
# Keep this endpoint available for future server-side storage / sharing.

KML_EXTS = {'kml'}
KML_SUBDIR = os.path.join('uploads', 'kml')   # under /static


@chimney_bp.route('/api/chimney-projects/<int:chimney_id>/kml', methods=['POST'])
def api_upload_kml(chimney_id):
    guard = _admin_guard()
    if guard:
        return guard
    project = ChimneyProject.query.get_or_404(chimney_id)

    file = request.files.get('kml_file')
    if not file or not file.filename:
        return jsonify({'error': 'No file uploaded.'}), 400
    if not _ext_ok(file.filename, KML_EXTS):
        return jsonify({'error': 'File must be a .kml file.'}), 400

    dest_dir = os.path.join(current_app.root_path, 'static', KML_SUBDIR, str(project.id))
    os.makedirs(dest_dir, exist_ok=True)

    safe_name = secure_filename(file.filename)
    dest_path = os.path.join(dest_dir, safe_name)
    file.save(dest_path)

    rel_path = os.path.relpath(dest_path, os.path.join(current_app.root_path, 'static'))
    kml_url = '/static/' + rel_path.replace(os.sep, '/')

    return jsonify({'kml_url': kml_url, 'filename': safe_name}), 201


@chimney_bp.route('/api/chimney-projects/<int:chimney_id>/kml', methods=['GET'])
def api_list_kml(chimney_id):
    guard = _login_guard()
    if guard:
        return guard
    project = ChimneyProject.query.get_or_404(chimney_id)

    kml_dir = os.path.join(current_app.root_path, 'static', KML_SUBDIR, str(project.id))
    files = []
    if os.path.isdir(kml_dir):
        for fname in sorted(os.listdir(kml_dir)):
            if fname.lower().endswith('.kml'):
                rel = os.path.join(KML_SUBDIR, str(project.id), fname).replace(os.sep, '/')
                files.append({'filename': fname, 'kml_url': '/static/' + rel})
    return jsonify({'kml_files': files})


# ── Defect report (PDF) ───────────────────────────────────────────────────────

@chimney_bp.route('/api/chimney-projects/<int:chimney_id>/report', methods=['GET', 'POST'])
def api_generate_defect_report(chimney_id):
    from flask import send_file
    guard = _login_guard()
    if guard:
        return guard
    project = ChimneyProject.query.get_or_404(chimney_id)
    defects = ChimneyDefect.query.filter_by(chimney_project_id=chimney_id) \
                                  .order_by(ChimneyDefect.created_at.asc()).all()

    # Optional full-chimney screenshot captured client-side (see
    # captureCoverImage() in chimney_viewer.js) and posted alongside the
    # report request, used on the cover page. Saved to a temp file so
    # reportlab can read it as a normal image path — never persisted.
    cover_image_tmp_path = None
    cover_file = request.files.get('cover_image')
    if cover_file and cover_file.filename:
        try:
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                cover_file.save(tmp.name)
                cover_image_tmp_path = tmp.name
        except Exception:
            current_app.logger.exception('Could not save cover image for report %s', chimney_id)
            cover_image_tmp_path = None

    try:
        from chimney_report import build_defect_report_pdf
        static_root = os.path.join(current_app.root_path, 'static')
        pdf_buf = build_defect_report_pdf(project, defects, static_root,
                                           cover_image_path=cover_image_tmp_path)
    except Exception as e:
        # Never let a bad/missing image or malformed defect field crash the
        # whole report silently -- log it and tell the client exactly what broke.
        current_app.logger.exception('Chimney report generation failed for project %s', chimney_id)
        return jsonify({'error': f'Report generation failed: {e}'}), 500
    finally:
        if cover_image_tmp_path and os.path.exists(cover_image_tmp_path):
            try:
                os.remove(cover_image_tmp_path)
            except OSError:
                pass

    safe_name = ''.join(c for c in project.asset_name if c.isalnum() or c in ' _-').strip().replace(' ', '_')
    download_name = f'{safe_name or "chimney"}_inspection_report.pdf'
    return send_file(pdf_buf, as_attachment=False, download_name=download_name, mimetype='application/pdf')
