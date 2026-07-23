"""
projects_routes.py — Blueprint for all dynamic Project / Division / Line data.

Replaces the old hardcoded MPPTCL / DVC project cards with a database-backed
system shared by every module:

    Module ("Transmission Line", "Land Survey", ...)
      └── Project   (+ Add Project: name, contact no, email, country, state, logo)
            └── Division   (Transmission Line only — + Add Division: name, lat, lng)
                  └── Line     (+ Add Line: name, start/end lat-lng, length, towers, KML)

Register in app.py with: app.register_blueprint(projects_bp)
"""
import os
import math
import json
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, current_app
from werkzeug.utils import secure_filename
from models import db, Project, Division, Line, ActivityLog, TowerPhoto, TowerDefect, TowerReport, User, TowerInspectionStatus, PilotAssignment
import settings as app_settings

projects_bp = Blueprint('projects_bp', __name__)

LOGO_EXTS  = {'jpg', 'jpeg', 'png'}
KML_EXTS   = {'kml', 'kmz'}
IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'webp'}

UPLOAD_BASE = os.path.join('static', 'uploads')


def _login_guard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return None


def _admin_guard():
    """Blocks write actions for anyone whose active session role isn't
    Admin — tower photo upload/defect marking/deletion is Admin-only;
    Client User sessions get view-only access."""
    guard = _login_guard()
    if guard:
        return guard
    if session.get('role') != 'Admin':
        return jsonify({'error': 'Your account has view-only access to this module.'}), 403
    return None


def _pilot_guard():
    guard = _login_guard()
    if guard:
        return guard
    if session.get('role') != 'Pilot':
        return jsonify({'error': 'This is only available to Pilot accounts.'}), 403
    return None


def _visible_project_ids(module_name):
    """None means "no restriction, show every project in this module" —
    Admin sessions always get this, and so does a Client User who hasn't
    had any specific projects assigned for this module (the
    backward-compatible default). Otherwise, the set of project IDs this
    Client User is actually allowed to see."""
    if session.get('role') == 'Admin':
        return None
    user = User.query.get(session.get('user_id'))
    if not user:
        return set()  # no valid session user — show nothing rather than guess
    return user.restricted_project_ids_for_module(module_name)


def _project_access_guard(project):
    """For direct-URL access to a specific project's pages (map/overview/
    info) — the list endpoint filtering above only helps if someone
    actually goes through the list; this closes the gap for anyone who
    has (or guesses) a direct link to a project they're not allowed to
    see. Returns a Flask response to abort with, or None if access is OK."""
    allowed_ids = _visible_project_ids(project.module)
    if allowed_ids is not None and project.id not in allowed_ids:
        return jsonify({'error': "You don't have access to this project."}), 403
    return None


def _ext_ok(filename, allowed):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed


def _save_upload(file_storage, subfolder, allowed_exts):
    """Save an uploaded file under static/uploads/<subfolder>/ and return the
    path relative to /static (or '' if no valid file was supplied)."""
    if not file_storage or not file_storage.filename:
        return ''
    if not _ext_ok(file_storage.filename, allowed_exts):
        return None  # signals an invalid file type to the caller
    base_dir = current_app.root_path if hasattr(current_app, 'root_path') else '.'
    folder_fs = os.path.join(base_dir, UPLOAD_BASE, subfolder)
    os.makedirs(folder_fs, exist_ok=True)
    safe_name = secure_filename(file_storage.filename)
    # avoid collisions
    name_root, name_ext = os.path.splitext(safe_name)
    final_name = safe_name
    i = 1
    while os.path.exists(os.path.join(folder_fs, final_name)):
        final_name = f"{name_root}_{i}{name_ext}"
        i += 1
    file_storage.save(os.path.join(folder_fs, final_name))
    return f"uploads/{subfolder}/{final_name}"


def _haversine_km(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# Photos attached to a tower must be taken within this many meters of it —
# a generous buffer for normal GPS inaccuracy, not a tight survey
# tolerance. A photo with no GPS EXIF data at all is rejected too (see
# api_upload_tower_photo) — drone photos reliably carry it, so a photo
# without it can't be verified as belonging to this tower.
TOWER_PHOTO_GPS_BUFFER_M = 70


def _extract_gps_from_image(file_storage):
    """Best-effort read of (lat, lng) from an uploaded image's EXIF GPS
    tags. Returns None if the image has no GPS data, isn't a format Pillow
    can read EXIF from, or anything else goes wrong — this is advisory
    validation, not something that should ever crash the upload.

    Uses Image.getexif() + get_ifd(GPSInfo) — Pillow's current, documented
    way to read the GPS IFD. The older/legacy Image._getexif() approach
    (which just returns whatever's under the 'GPSInfo' key of its flat
    dict) doesn't reliably decode the nested GPS IFD across Pillow
    versions — it can come back as a bare IFD offset/reference rather than
    the actual decoded tag dict, silently failing to find real GPS data
    even in images (like standard drone photos) that do have it."""
    try:
        from PIL import Image, ExifTags
        file_storage.stream.seek(0)
        img = Image.open(file_storage.stream)
        exif = img.getexif()
        file_storage.stream.seek(0)  # rewind so _save_upload can still read the full file
        if not exif:
            return None
        gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo)
        if not gps_ifd:
            return None
        gps = {ExifTags.GPSTAGS.get(k, k): v for k, v in gps_ifd.items()}

        def _to_degrees(value):
            d, m, s = value[0], value[1], value[2]
            return float(d) + float(m) / 60.0 + float(s) / 3600.0

        if 'GPSLatitude' not in gps or 'GPSLongitude' not in gps:
            return None
        lat = _to_degrees(gps['GPSLatitude'])
        if gps.get('GPSLatitudeRef') != 'N':
            lat = -lat
        lng = _to_degrees(gps['GPSLongitude'])
        if gps.get('GPSLongitudeRef') != 'E':
            lng = -lng
        return (lat, lng)
    except Exception:
        try:
            file_storage.stream.seek(0)
        except Exception:
            pass
        return None


# ── Projects ────────────────────────────────────────────────────────────────

@projects_bp.route('/api/projects', methods=['GET'])
def api_list_projects():
    guard = _login_guard()
    if guard:
        return guard
    module = request.args.get('module', '')
    q = Project.query
    if module:
        q = q.filter_by(module=module)
    projects = q.order_by(Project.created_at.asc()).all()

    # Project-wise access restriction (on top of module-level access,
    # which already gated the page/nav getting here) — only actually
    # narrows anything down for a Client User who's had specific projects
    # assigned; everyone else sees the full list unfiltered.
    if module:
        allowed_ids = _visible_project_ids(module)
        if allowed_ids is not None:
            projects = [p for p in projects if p.id in allowed_ids]

    return jsonify({'projects': [p.to_dict() for p in projects]})


@projects_bp.route('/api/projects', methods=['POST'])
def api_create_project():
    guard = _login_guard()
    if guard:
        return guard

    form = request.form
    name = (form.get('name') or '').strip()
    module = (form.get('module') or '').strip()
    email = (form.get('email') or '').strip()

    if not name:
        return jsonify({'error': 'Project name is required.'}), 400
    if not module:
        return jsonify({'error': 'Module is required.'}), 400
    if email and '@' not in email:
        return jsonify({'error': 'Invalid email address.'}), 400

    logo_path = ''
    logo_file = request.files.get('logo')
    if logo_file and logo_file.filename:
        saved = _save_upload(logo_file, 'logos', LOGO_EXTS)
        if saved is None:
            return jsonify({'error': 'Company logo must be a .jpg or .png file.'}), 400
        logo_path = saved

    def _int_or_none(raw):
        raw = (raw or '').strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    project = Project(
        module=module,
        name=name,
        contact_no=(form.get('contact_no') or '').strip(),
        email=email,
        country=(form.get('country') or '').strip(),
        state=(form.get('state') or '').strip(),
        logo_path=logo_path,
        client_name=(form.get('client_name') or '').strip(),
        planned_divisions=_int_or_none(form.get('planned_divisions')),
        planned_towers=_int_or_none(form.get('planned_towers')),
        timeline=(form.get('timeline') or '').strip(),
        created_by=session.get('user_id'),
    )
    db.session.add(project)
    db.session.commit()
    return jsonify(project.to_dict()), 201


@projects_bp.route('/api/projects/<int:project_id>', methods=['GET'])
def api_get_project(project_id):
    guard = _login_guard()
    if guard:
        return guard
    project = Project.query.get_or_404(project_id)
    data = project.to_dict()
    data['divisions'] = [d.to_dict() for d in project.divisions]
    return jsonify(data)


@projects_bp.route('/api/projects/<int:project_id>', methods=['DELETE'])
def api_delete_project(project_id):
    guard = _login_guard()
    if guard:
        return guard
    project = Project.query.get_or_404(project_id)

    data = request.get_json(force=True, silent=True) or {}
    password = (data.get('password') or request.args.get('password') or '').strip()
    if not app_settings.verify_delete_password(password):
        return jsonify({'error': 'Incorrect delete password.'}), 403

    name, module = project.name, project.module
    db.session.delete(project)
    ActivityLog.log(action='delete', entity_type='Project', entity_name=name,
                     module=module, performed_by=session.get('user_name', ''))
    db.session.commit()
    return jsonify({'deleted': project_id})


# ── Project map page (dynamic Transmission Line view) ────────────────────────

@projects_bp.route('/projects/<int:project_id>/map')
def project_map(project_id):
    guard = _login_guard()
    if guard:
        return guard
    project = Project.query.get_or_404(project_id)
    guard = _project_access_guard(project)
    if guard:
        return guard
    from users import MODULE_ROUTES
    back_endpoint = MODULE_ROUTES.get(project.module, 'projects')
    is_admin = session.get('role') == 'Admin'
    ActivityLog.log(action='enter_project', entity_type='Project', entity_name=project.name,
                     module=project.module, performed_by=session.get('user_name', ''), role=session.get('role', ''))
    db.session.commit()
    return render_template('project_map.html', project=project, user_name=session.get('user_name', ''),
                            back_endpoint=back_endpoint, is_admin=is_admin)


def _build_project_defect_summary(project):
    """Every stat, chart-ready number, and grouped defect list needed for
    both the web Overview page and the PDF report — computed once here so
    the two can never drift out of sync with each other. Web-only
    presentation details (the CSS conic-gradient string, bar-chart
    percentages) are layered on top of this in project_overview() rather
    than baked in here, since the PDF report has no use for them."""
    divisions = project.divisions
    lines = [l for d in divisions for l in d.lines]
    line_ids = [l.id for l in lines]

    # line_id -> (line, division), so each defect row can show which line
    # and division it came from without a query per row.
    line_lookup = {}
    for d in divisions:
        for l in d.lines:
            line_lookup[l.id] = (l, d)

    defects = []
    if line_ids:
        defects = (TowerDefect.query
                   .join(TowerPhoto, TowerDefect.tower_photo_id == TowerPhoto.id)
                   .filter(TowerPhoto.line_id.in_(line_ids))
                   .order_by(TowerDefect.created_at.desc()).all())

    severity_counts = {'Critical': 0, 'Major': 0, 'Minor': 0}
    division_defect_counts = {}
    defect_rows = []
    photographed_towers = set()
    for defect in defects:
        photo = defect.photo
        line, division = line_lookup.get(photo.line_id, (None, None))
        sev = defect.severity if defect.severity in severity_counts else 'Minor'
        severity_counts[sev] += 1
        div_name = division.name if division else '—'
        division_defect_counts[div_name] = division_defect_counts.get(div_name, 0) + 1
        photographed_towers.add((photo.line_id, photo.tower_label))
        try:
            shape_coords = json.loads(defect.shape_coords) if defect.shape_coords else []
        except (TypeError, ValueError):
            shape_coords = []
        defect_rows.append({
            'id': defect.id,
            'component_name': defect.component_name,
            'severity': defect.severity,
            'location': defect.location,
            'observation': defect.observation,
            'comments': defect.comments,
            'tower_label': photo.tower_label,
            'line_id': photo.line_id,
            'line_name': line.name if line else '—',
            'division_name': div_name,
            'created_at': defect.created_at,
            'created_by': defect.created_by,
            'image_path': photo.image_path or '',
            'image_url': f'/static/{photo.image_path}' if photo.image_path else '',
            'shape_type': defect.shape_type,
            'shape_coords': shape_coords,
        })

    total_defects = len(defect_rows)

    # Per-division breakdown — lines, towers, and defects for each division
    # on its own, not just the project-wide totals.
    division_stats = []
    for d in divisions:
        d_lines = d.lines
        d_towers = sum(l.tower_count or 0 for l in d_lines)
        division_stats.append({
            'name': d.name,
            'line_count': len(d_lines),
            'tower_count': d_towers,
            'defect_count': division_defect_counts.get(d.name, 0),
        })

    # Defects grouped by tower (division -> line -> tower), so both the
    # web table and the PDF report read as "here's everything found at
    # this tower" instead of one flat list — ordered by division name,
    # then line name, then tower label.
    tower_groups_map = {}
    tower_group_order = []
    for row in defect_rows:
        key = (row['division_name'], row['line_name'], row['tower_label'])
        if key not in tower_groups_map:
            tower_groups_map[key] = []
            tower_group_order.append(key)
        tower_groups_map[key].append(row)
    tower_group_order.sort(key=lambda k: (k[0], k[1], k[2]))
    tower_groups = [
        {
            'division_name': key[0], 'line_name': key[1], 'tower_label': key[2],
            'defects': tower_groups_map[key],
        }
        for key in tower_group_order
    ]

    return {
        'division_count': len(divisions),
        'line_count': len(lines),
        'tower_count': sum(l.tower_count or 0 for l in lines),
        'towers_photographed': len(photographed_towers),
        'total_defects': total_defects,
        'severity_counts': severity_counts,
        'division_defect_counts': division_defect_counts,
        'division_stats': division_stats,
        'defect_rows': defect_rows,
        'tower_groups': tower_groups,
    }


@projects_bp.route('/projects/<int:project_id>/overview')
def project_overview(project_id):
    guard = _login_guard()
    if guard:
        return guard
    project = Project.query.get_or_404(project_id)
    guard = _project_access_guard(project)
    if guard:
        return guard
    from users import MODULE_ROUTES
    back_endpoint = MODULE_ROUTES.get(project.module, 'projects')
    is_admin = session.get('role') == 'Admin'

    s = _build_project_defect_summary(project)
    total_defects = s['total_defects']
    severity_counts = s['severity_counts']
    division_defect_counts = s['division_defect_counts']

    # Severity pie — same CSS conic-gradient technique as the chimney
    # module's overview page. Web-only presentation, not part of the
    # shared summary above.
    def _deg(n):
        return round((n / total_defects * 360), 2) if total_defects else 0
    crit_deg = _deg(severity_counts['Critical'])
    major_deg = _deg(severity_counts['Major'])
    if total_defects:
        severity_pie_gradient = (
            f"conic-gradient(var(--danger) 0deg {crit_deg}deg, "
            f"#e0a53a {crit_deg}deg {crit_deg + major_deg}deg, "
            f"var(--success) {crit_deg + major_deg}deg 360deg)"
        )
    else:
        severity_pie_gradient = 'conic-gradient(var(--border) 0deg 360deg)'

    # Bar chart — defects per division, tallest first.
    max_div_count = max(division_defect_counts.values()) if division_defect_counts else 1
    division_bars = [
        {'name': name, 'count': count, 'pct': round(count / max_div_count * 100)}
        for name, count in sorted(division_defect_counts.items(), key=lambda kv: -kv[1])
    ]

    return render_template('project_overview.html',
        project=project, user_name=session.get('user_name', ''), is_admin=is_admin,
        back_endpoint=back_endpoint,
        division_count=s['division_count'], line_count=s['line_count'], tower_count=s['tower_count'],
        towers_photographed=s['towers_photographed'],
        total_defects=total_defects, severity_counts=severity_counts,
        severity_pie_gradient=severity_pie_gradient, division_bars=division_bars,
        division_stats=s['division_stats'], tower_groups=s['tower_groups'],
        defect_rows=s['defect_rows'],
    )


@projects_bp.route('/projects/<int:project_id>/report')
def project_defect_report(project_id):
    """Generates and streams the defect report PDF for download/view.
    Open to both Admin and Client sessions, same as the chimney module's
    report — reuses _build_project_defect_summary() so the numbers in this
    PDF are always identical to what's shown on the Overview page."""
    guard = _login_guard()
    if guard:
        return guard
    project = Project.query.get_or_404(project_id)
    guard = _project_access_guard(project)
    if guard:
        return guard
    summary = _build_project_defect_summary(project)

    try:
        from trans_report import build_trans_report_pdf
        static_root = os.path.join(current_app.root_path, 'static')
        pdf_buf = build_trans_report_pdf(project, summary, static_root)
    except Exception as e:
        current_app.logger.exception('TRANS report generation failed for project %s', project_id)
        return jsonify({'error': f'Report generation failed: {e}'}), 500

    from flask import send_file
    safe_name = ''.join(c for c in project.name if c.isalnum() or c in ' _-').strip().replace(' ', '_')
    download_name = f'{safe_name or "transmission_line"}_inspection_report.pdf'
    return send_file(pdf_buf, as_attachment=False, download_name=download_name, mimetype='application/pdf')


@projects_bp.route('/projects/<int:project_id>/info')
def project_info(project_id):
    guard = _login_guard()
    if guard:
        return guard
    project = Project.query.get_or_404(project_id)
    guard = _project_access_guard(project)
    if guard:
        return guard
    return render_template('project_info.html', project=project, user_name=session.get('user_name', ''))


# ── Divisions ──────────────────────────────────────────────────────────────

@projects_bp.route('/api/projects/<int:project_id>/divisions', methods=['GET'])
def api_list_divisions(project_id):
    guard = _login_guard()
    if guard:
        return guard
    Project.query.get_or_404(project_id)
    divisions = Division.query.filter_by(project_id=project_id).order_by(Division.created_at.asc()).all()
    return jsonify({'divisions': [d.to_dict() for d in divisions]})


@projects_bp.route('/api/projects/<int:project_id>/divisions', methods=['POST'])
def api_create_division(project_id):
    guard = _login_guard()
    if guard:
        return guard
    Project.query.get_or_404(project_id)

    data = request.get_json(force=True, silent=True) or request.form
    name = (data.get('name') or '').strip()
    try:
        lat = float(data.get('latitude'))
        lng = float(data.get('longitude'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Latitude and longitude must be valid numbers.'}), 400

    if not name:
        return jsonify({'error': 'Division name is required.'}), 400
    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        return jsonify({'error': 'Latitude/longitude out of range.'}), 400

    planned_towers_raw = (data.get('planned_towers') or '').strip() if isinstance(data.get('planned_towers'), str) else data.get('planned_towers')
    planned_towers = None
    if planned_towers_raw not in (None, ''):
        try:
            planned_towers = int(planned_towers_raw)
        except (TypeError, ValueError):
            planned_towers = None

    division = Division(
        project_id=project_id, name=name, latitude=lat, longitude=lng,
        client_name=(data.get('client_name') or '').strip() if isinstance(data.get('client_name'), str) else '',
        state=(data.get('state') or '').strip() if isinstance(data.get('state'), str) else '',
        planned_towers=planned_towers,
    )
    db.session.add(division)
    db.session.commit()
    return jsonify(division.to_dict()), 201


@projects_bp.route('/api/divisions/<int:division_id>', methods=['DELETE'])
def api_delete_division(division_id):
    guard = _login_guard()
    if guard:
        return guard
    division = Division.query.get_or_404(division_id)
    db.session.delete(division)
    db.session.commit()
    return jsonify({'deleted': division_id})


# ── Lines ──────────────────────────────────────────────────────────────────

@projects_bp.route('/api/divisions/<int:division_id>/lines', methods=['GET'])
def api_list_lines(division_id):
    guard = _login_guard()
    if guard:
        return guard
    Division.query.get_or_404(division_id)
    lines = Line.query.filter_by(division_id=division_id).order_by(Line.created_at.asc()).all()
    return jsonify({'lines': [l.to_dict() for l in lines]})


@projects_bp.route('/api/divisions/<int:division_id>/lines', methods=['POST'])
def api_create_line(division_id):
    guard = _login_guard()
    if guard:
        return guard
    Division.query.get_or_404(division_id)

    form = request.form
    name = (form.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Line name is required.'}), 400

    try:
        start_lat = float(form.get('start_lat'))
        start_lng = float(form.get('start_lng'))
        end_lat   = float(form.get('end_lat'))
        end_lng   = float(form.get('end_lng'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Start/end position must be valid lat, long numbers.'}), 400

    length_raw = (form.get('length_km') or '').strip()
    if length_raw:
        try:
            length_km = float(length_raw)
        except ValueError:
            return jsonify({'error': 'Line length must be a number.'}), 400
    else:
        length_km = round(_haversine_km(start_lat, start_lng, end_lat, end_lng), 2)

    try:
        tower_count = int(form.get('tower_count') or 0)
    except ValueError:
        return jsonify({'error': 'Number of towers must be a whole number.'}), 400

    kml_path = ''
    kml_file = request.files.get('kml_file')
    if kml_file and kml_file.filename:
        saved = _save_upload(kml_file, 'kml', KML_EXTS)
        if saved is None:
            return jsonify({'error': 'KML file must have a .kml or .kmz extension.'}), 400
        kml_path = saved

    line = Line(
        division_id=division_id,
        name=name,
        start_lat=start_lat, start_lng=start_lng,
        end_lat=end_lat, end_lng=end_lng,
        length_km=length_km,
        tower_count=tower_count,
        kml_path=kml_path,
    )
    db.session.add(line)
    db.session.commit()
    return jsonify(line.to_dict()), 201


@projects_bp.route('/api/lines/<int:line_id>', methods=['DELETE'])
def api_delete_line(line_id):
    guard = _login_guard()
    if guard:
        return guard
    line = Line.query.get_or_404(line_id)
    db.session.delete(line)
    db.session.commit()
    return jsonify({'deleted': line_id})


# ── Tower photos ─────────────────────────────────────────────────────────
# Tower points come from parsing a Line's KML client-side (not individual
# DB rows) — photos are matched to a specific point by line_id + the
# tower's label as it appears in the KML (e.g. "T12"), passed by the client
# exactly as shown in the tower details panel.

@projects_bp.route('/api/lines/<int:line_id>/tower-photos', methods=['GET'])
def api_list_tower_photos(line_id):
    guard = _login_guard()
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    tower_label = (request.args.get('tower') or '').strip()
    if not tower_label:
        return jsonify({'error': 'tower is required.'}), 400
    photos = (TowerPhoto.query
              .filter_by(line_id=line_id, tower_label=tower_label)
              .order_by(TowerPhoto.created_at.asc()).all())
    # Client sessions only ever see a tower's photos once Admin has
    # explicitly marked that tower's inspection as done — NOT just
    # whether defects happen to be marked. A tower with zero defects is
    # either a genuinely good tower or one nobody's finished reviewing
    # yet; "inspection done" is how Admin distinguishes those, so a good
    # tower still needs that explicit sign-off before a client sees it,
    # same as a tower with real defects does. Enforced here, not just
    # hidden in the UI, so a Client session can't see everything by
    # calling this endpoint directly. Pilot is deliberately excluded from
    # this restriction — a pilot needs to see everything THEY'VE captured
    # regardless of whether Admin has finished reviewing it yet.
    if session.get('role') == 'Client User':
        status = TowerInspectionStatus.query.filter_by(line_id=line_id, tower_label=tower_label).first()
        if not status or not status.inspection_done:
            photos = []
    return jsonify({'photos': [p.to_dict() for p in photos]})


@projects_bp.route('/api/lines/<int:line_id>/tower-photos', methods=['POST'])
def api_upload_tower_photo(line_id):
    guard = _admin_guard()
    if guard:
        return guard
    Line.query.get_or_404(line_id)

    tower_label = (request.form.get('tower') or '').strip()
    if not tower_label:
        return jsonify({'error': 'tower is required.'}), 400

    image_file = request.files.get('image')
    if not image_file or not image_file.filename:
        return jsonify({'error': 'An image file is required.'}), 400

    # GPS proximity check: an uploaded photo should actually have been
    # taken at this tower, not some other one. Read the tower's own
    # coordinates from the form (the client has them from the KML point
    # that was clicked), read the photo's own EXIF GPS tag, and compare —
    # drone photos reliably carry accurate GPS in their EXIF, so this is a
    # straightforward accept/reject: within range, upload; too far away,
    # or no GPS data on the photo at all, reject.
    try:
        tower_lat = float(request.form.get('tower_lat'))
        tower_lng = float(request.form.get('tower_lng'))
    except (TypeError, ValueError):
        tower_lat = tower_lng = None

    if tower_lat is not None and tower_lng is not None:
        gps = _extract_gps_from_image(image_file)
        if gps is None:
            return jsonify({
                'error': "This photo has no location data in it, so it can't be verified against this tower. "
                         "Please upload a photo that has GPS data."
            }), 400
        dist_m = _haversine_km(gps[0], gps[1], tower_lat, tower_lng) * 1000
        if dist_m > TOWER_PHOTO_GPS_BUFFER_M:
            return jsonify({
                'error': f"This photo's location is about {round(dist_m)}m from this tower — photos must be "
                         f"taken within {TOWER_PHOTO_GPS_BUFFER_M}m. It looks like it may belong to a "
                         f"different tower."
            }), 400

    saved = _save_upload(image_file, 'tower_photos', IMAGE_EXTS)
    if saved is None:
        return jsonify({'error': 'Image must be a .jpg, .png, or .webp file.'}), 400
    if saved == '':
        return jsonify({'error': 'An image file is required.'}), 400

    photo = TowerPhoto(
        line_id=line_id,
        tower_label=tower_label,
        image_path=saved,
        uploaded_by=session.get('user_name', ''),
    )
    db.session.add(photo)
    db.session.commit()
    return jsonify(photo.to_dict()), 201


@projects_bp.route('/api/lines/<int:line_id>/tower-defects-flat', methods=['GET'])
def api_tower_defects_flat(line_id):
    """One entry PER DEFECT (not per photo) for a tower, each carrying its
    parent photo's image_url. Used for the client-facing gallery: marking
    2 defects on the same uploaded photo should show as 2 separate image
    entries there — one per defect, each highlighting just that one
    marking — rather than one image with both defects combined."""
    guard = _login_guard()
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    tower_label = (request.args.get('tower') or '').strip()
    if not tower_label:
        return jsonify({'error': 'tower is required.'}), 400
    defects = (TowerDefect.query
               .join(TowerPhoto, TowerDefect.tower_photo_id == TowerPhoto.id)
               .filter(TowerPhoto.line_id == line_id, TowerPhoto.tower_label == tower_label)
               .order_by(TowerDefect.created_at.asc()).all())
    out = []
    for d in defects:
        entry = d.to_dict()
        entry['image_url'] = f'/static/{d.photo.image_path}' if d.photo.image_path else ''
        out.append(entry)
    return jsonify({'defects': out})


@projects_bp.route('/api/divisions/<int:division_id>/photo-coverage', methods=['GET'])
def api_division_photo_coverage(division_id):
    """For every line in this division: how many of its towers have at
    least one photo, out of its total tower_count. One request covers
    the whole division's sidebar at once, rather than one request per
    line — avoids an N+1 pattern when a division has many lines."""
    guard = _login_guard()
    if guard:
        return guard
    division = Division.query.get_or_404(division_id)
    lines = division.lines
    line_ids = [l.id for l in lines]

    photographed_counts = {}
    if line_ids:
        rows = (db.session.query(TowerPhoto.line_id, TowerPhoto.tower_label)
                .filter(TowerPhoto.line_id.in_(line_ids)).distinct().all())
        for line_id, _label in rows:
            photographed_counts[line_id] = photographed_counts.get(line_id, 0) + 1

    return jsonify({
        'coverage': {
            l.id: {'photographed': photographed_counts.get(l.id, 0), 'total': l.tower_count or 0}
            for l in lines
        }
    })


@projects_bp.route('/api/lines/<int:line_id>/tower-photo-labels', methods=['GET'])
def api_tower_photo_labels(line_id):
    """Which tower labels on this line have at least one photo — used for
    the map's photo-indicator dots and the Tower Photo Checklist. Admin
    and Pilot both see every uploaded label regardless of inspection
    status — Admin needs to catch what's missing, Pilot needs to see
    everything they've captured regardless of whether Admin's reviewed
    it yet. A Client session only ever sees labels for towers whose
    inspection has been explicitly marked done, matching
    api_list_tower_photos — otherwise a Client's map dot would point at a
    tower whose photos, once opened, turn out to be empty for them."""
    guard = _login_guard()
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    q = db.session.query(TowerPhoto.tower_label).filter_by(line_id=line_id)
    if session.get('role') == 'Client User':
        done_labels = {s.tower_label for s in TowerInspectionStatus.query.filter_by(line_id=line_id, inspection_done=True).all()}
        q = q.filter(TowerPhoto.tower_label.in_(done_labels)) if done_labels else q.filter(db.false())
    rows = q.distinct().all()
    return jsonify({'labels': [r[0] for r in rows]})


@projects_bp.route('/api/lines/<int:line_id>/towers/<path:tower_label>/inspection-status', methods=['GET'])
def api_get_inspection_status(line_id, tower_label):
    guard = _login_guard()
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    tower_label = tower_label.strip()
    status = TowerInspectionStatus.query.filter_by(line_id=line_id, tower_label=tower_label).first()
    if status:
        return jsonify(status.to_dict())
    return jsonify({'line_id': line_id, 'tower_label': tower_label, 'inspection_done': False, 'marked_by': '', 'marked_at': ''})


@projects_bp.route('/api/lines/<int:line_id>/towers/<path:tower_label>/inspection-status', methods=['POST'])
def api_set_inspection_status(line_id, tower_label):
    guard = _admin_guard()
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    tower_label = tower_label.strip()
    data = request.get_json(force=True, silent=True) or {}
    done = bool(data.get('inspection_done'))

    status = TowerInspectionStatus.query.filter_by(line_id=line_id, tower_label=tower_label).first()
    if not status:
        status = TowerInspectionStatus(line_id=line_id, tower_label=tower_label)
        db.session.add(status)
    status.inspection_done = done
    status.marked_by = session.get('user_name', '') if done else ''
    status.marked_at = datetime.utcnow() if done else None
    db.session.commit()
    return jsonify(status.to_dict())


@projects_bp.route('/api/pilots', methods=['GET'])
def api_list_pilots():
    """Users with the Pilot role, for the "Assign Pilot" dropdown when
    uploading/editing a Line's KML."""
    guard = _admin_guard()
    if guard:
        return guard
    from models import Role
    pilot_role = Role.query.filter_by(name='Pilot').first()
    pilots = pilot_role.users if pilot_role else []
    return jsonify({'pilots': [{'id': p.id, 'username': p.username, 'email': p.email} for p in pilots]})


@projects_bp.route('/api/lines/<int:line_id>/assign-pilot', methods=['GET'])
def api_get_line_assignment(line_id):
    guard = _admin_guard()
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    assignment = PilotAssignment.query.filter_by(line_id=line_id).first()
    return jsonify({'assignment': assignment.to_dict() if assignment else None})


@projects_bp.route('/api/lines/<int:line_id>/assign-pilot', methods=['POST'])
def api_assign_pilot(line_id):
    guard = _admin_guard()
    if guard:
        return guard
    line = Line.query.get_or_404(line_id)
    data = request.get_json(force=True, silent=True) or {}
    pilot_user_id = data.get('pilot_user_id')

    if not pilot_user_id:
        # Explicit unassign — clears whoever's currently on this line.
        PilotAssignment.query.filter_by(line_id=line_id).delete()
        db.session.commit()
        return jsonify({'ok': True, 'assignment': None})

    pilot = User.query.get(int(pilot_user_id))
    if not pilot or 'Pilot' not in pilot.role_names():
        return jsonify({'error': 'That user does not have the Pilot role.'}), 400

    # One pilot per line at a time — reassigning replaces rather than adds.
    PilotAssignment.query.filter_by(line_id=line_id).delete()
    assignment = PilotAssignment(
        line_id=line_id, pilot_user_id=pilot.id,
        assigned_by=session.get('user_name', ''), seen_by_pilot=False,
    )
    db.session.add(assignment)
    ActivityLog.log(action='assign_pilot', entity_type='Line', entity_name=line.name,
                     performed_by=session.get('user_name', ''), role=session.get('role', ''),
                     details=f'Assigned to pilot {pilot.username}')
    db.session.commit()
    return jsonify({'ok': True, 'assignment': assignment.to_dict()})


@projects_bp.route('/api/pilot/assignments', methods=['GET'])
def api_pilot_assignments():
    """The current Pilot's own assigned work — what their dashboard's
    "Assigned Work" list and new-assignment popup are built from."""
    guard = _pilot_guard()
    if guard:
        return guard
    rows = (PilotAssignment.query
            .filter_by(pilot_user_id=session.get('user_id'))
            .order_by(PilotAssignment.assigned_at.desc()).all())
    out = []
    for a in rows:
        line = a.line
        division = line.division if line else None
        project = division.project if division else None
        entry = a.to_dict()
        entry['line_name'] = line.name if line else '—'
        entry['division_name'] = division.name if division else '—'
        entry['project_name'] = project.name if project else '—'
        entry['project_module'] = project.module if project else ''
        entry['kml_url'] = f'/static/{line.kml_path}' if line and line.kml_path else ''
        out.append(entry)
    return jsonify({'assignments': out})


@projects_bp.route('/api/pilot/assignments/<int:assignment_id>/acknowledge', methods=['POST'])
def api_acknowledge_assignment(assignment_id):
    guard = _pilot_guard()
    if guard:
        return guard
    assignment = PilotAssignment.query.get_or_404(assignment_id)
    if assignment.pilot_user_id != session.get('user_id'):
        return jsonify({'error': 'Not your assignment.'}), 403
    assignment.seen_by_pilot = True
    db.session.commit()
    return jsonify({'ok': True})


@projects_bp.route('/api/lines/<int:line_id>/tower-photos/pilot-capture', methods=['POST'])
def api_pilot_capture_photo(line_id):
    """Upload path for the Pilot's in-app live camera capture.

    Unlike api_upload_tower_photo above (which reads GPS from the
    photo's own EXIF), a canvas-captured frame from getUserMedia has NO
    EXIF data at all — there's nothing embedded to check. So this
    validates against capture_lat/capture_lng instead: the device's live
    GPS reading taken via navigator.geolocation at the moment the pilot
    pressed the shutter, sent alongside the image. Same 70m buffer and
    same rejection behavior as the EXIF-based path, just a different
    source of truth for "where was this actually taken.\""""
    guard = _pilot_guard()
    if guard:
        return guard
    line = Line.query.get_or_404(line_id)

    tower_label = (request.form.get('tower') or '').strip()
    if not tower_label:
        return jsonify({'error': 'tower is required.'}), 400

    image_file = request.files.get('image')
    if not image_file or not image_file.filename:
        return jsonify({'error': 'An image file is required.'}), 400

    try:
        tower_lat = float(request.form.get('tower_lat'))
        tower_lng = float(request.form.get('tower_lng'))
        capture_lat = float(request.form.get('capture_lat'))
        capture_lng = float(request.form.get('capture_lng'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Missing location data — GPS must be available to capture.'}), 400

    dist_m = _haversine_km(capture_lat, capture_lng, tower_lat, tower_lng) * 1000
    if dist_m > TOWER_PHOTO_GPS_BUFFER_M:
        return jsonify({
            'error': f"You're about {round(dist_m)}m from this tower — you need to be within "
                     f"{TOWER_PHOTO_GPS_BUFFER_M}m to capture it. Please move to the correct location."
        }), 400

    saved = _save_upload(image_file, 'tower_photos', IMAGE_EXTS)
    if saved is None:
        return jsonify({'error': 'Image must be a .jpg, .png, or .webp file.'}), 400
    if saved == '':
        return jsonify({'error': 'An image file is required.'}), 400

    photo = TowerPhoto(
        line_id=line_id, tower_label=tower_label, image_path=saved,
        uploaded_by=session.get('user_name', ''),
    )
    db.session.add(photo)
    ActivityLog.log(action='capture_photo', entity_type='TowerPhoto',
                     entity_name=f"Tower {tower_label} — {line.name}",
                     module='TRANS', performed_by=session.get('user_name', ''), role='Pilot',
                     details=f'Captured within {round(dist_m)}m of tower location')
    db.session.commit()
    return jsonify(photo.to_dict()), 201


@projects_bp.route('/api/tower-photos/<int:photo_id>', methods=['DELETE'])
def api_delete_tower_photo(photo_id):
    guard = _admin_guard()
    if guard:
        return guard
    photo = TowerPhoto.query.get_or_404(photo_id)
    db.session.delete(photo)
    db.session.commit()
    return jsonify({'deleted': photo_id})


# ── Tower defects (marked directly on a tower photo) ────────────────────────
# The 2D equivalent of the chimney module's 3D defect markings — a
# polygon/rectangle/circle drawn over the photo, plus a short observation
# form. Viewing is open to both Admin and Client sessions; marking/editing/
# deleting is Admin-only (Client sessions see the marked-up photos but
# can't add to them).

VALID_SHAPE_TYPES = {'polygon', 'rect', 'circle'}
VALID_LOCATIONS = {'Top', 'Middle', 'Bottom'}
VALID_SEVERITIES = {'Minor', 'Major', 'Critical'}


@projects_bp.route('/api/tower-photos/<int:photo_id>/defects', methods=['GET'])
def api_list_tower_defects(photo_id):
    guard = _login_guard()
    if guard:
        return guard
    TowerPhoto.query.get_or_404(photo_id)
    defects = (TowerDefect.query.filter_by(tower_photo_id=photo_id)
               .order_by(TowerDefect.created_at.asc()).all())
    return jsonify({'defects': [d.to_dict() for d in defects]})


@projects_bp.route('/api/tower-photos/<int:photo_id>/defects', methods=['POST'])
def api_create_tower_defect(photo_id):
    guard = _admin_guard()
    if guard:
        return guard
    photo = TowerPhoto.query.get_or_404(photo_id)

    data = request.get_json(force=True, silent=True) or {}
    shape_type = (data.get('shape_type') or '').strip()
    if shape_type not in VALID_SHAPE_TYPES:
        return jsonify({'error': 'shape_type must be one of: polygon, rect, circle.'}), 400

    shape_coords = data.get('shape_coords')
    if not isinstance(shape_coords, list) or len(shape_coords) < 2:
        return jsonify({'error': 'shape_coords must be a list of at least 2 {x, y} points.'}), 400
    try:
        clean_coords = []
        for pt in shape_coords:
            x, y = float(pt['x']), float(pt['y'])
            if not (0 <= x <= 100) or not (0 <= y <= 100):
                return jsonify({'error': 'shape_coords must be percentages between 0 and 100.'}), 400
            clean_coords.append({'x': x, 'y': y})
    except (KeyError, TypeError, ValueError):
        return jsonify({'error': 'Each shape_coords point needs numeric x and y.'}), 400

    location = (data.get('location') or '').strip()
    if location and location not in VALID_LOCATIONS:
        return jsonify({'error': 'location must be one of: Top, Middle, Bottom.'}), 400

    severity = (data.get('severity') or 'Minor').strip()
    if severity not in VALID_SEVERITIES:
        severity = 'Minor'

    defect = TowerDefect(
        tower_photo_id=photo_id,
        shape_type=shape_type,
        shape_coords=json.dumps(clean_coords),
        component_name=(data.get('component_name') or '').strip(),
        location=location,
        defect_type=(data.get('defect_type') or '').strip(),
        observation=(data.get('observation') or '').strip(),
        severity=severity,
        status='Open',
        comments=(data.get('comments') or '').strip(),
        created_by=session.get('user_name', ''),
    )
    db.session.add(defect)
    ActivityLog.log(action='mark_defect', entity_type='TowerDefect',
                     entity_name=f"{defect.component_name or 'Defect'} — Tower {photo.tower_label}",
                     performed_by=session.get('user_name', ''), role=session.get('role', ''))
    db.session.commit()
    return jsonify(defect.to_dict()), 201


@projects_bp.route('/api/tower-defects/<int:defect_id>', methods=['DELETE'])
def api_delete_tower_defect(defect_id):
    guard = _admin_guard()
    if guard:
        return guard
    defect = TowerDefect.query.get_or_404(defect_id)
    db.session.delete(defect)
    db.session.commit()
    return jsonify({'deleted': defect_id})


# ── Per-tower RGB Visual Inspection Report ──────────────────────────────
# Generation is Admin-only; once generated, the PDF is stored on disk with
# a TowerReport row pointing at it, so a Client session can download the
# already-generated report without being able to trigger generation
# itself. Re-generating replaces the existing file/row for that tower
# rather than accumulating duplicates.

@projects_bp.route('/api/lines/<int:line_id>/tower-report', methods=['GET'])
def api_get_tower_report(line_id):
    """Does a report already exist for this tower? Used by the panel to
    decide whether to show a "Download Report" link."""
    guard = _login_guard()
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    tower_label = (request.args.get('tower') or '').strip()
    if not tower_label:
        return jsonify({'error': 'tower is required.'}), 400
    report = (TowerReport.query
              .filter_by(line_id=line_id, tower_label=tower_label)
              .order_by(TowerReport.generated_at.desc()).first())
    return jsonify({'report': report.to_dict() if report else None})


@projects_bp.route('/api/lines/<int:line_id>/tower-report', methods=['POST'])
def api_generate_tower_report(line_id):
    guard = _admin_guard()
    if guard:
        return guard
    line = Line.query.get_or_404(line_id)

    data = request.get_json(force=True, silent=True) or {}
    tower_label = (data.get('tower') or '').strip()
    if not tower_label:
        return jsonify({'error': 'tower is required.'}), 400

    status = TowerInspectionStatus.query.filter_by(line_id=line_id, tower_label=tower_label).first()
    if not status or not status.inspection_done:
        return jsonify({'error': 'This tower has not been marked "Inspection Done" yet — mark it done before generating a report.'}), 400

    try:
        tower_lat = float(data.get('tower_lat'))
        tower_lng = float(data.get('tower_lng'))
        coordinates = f'{tower_lat:.5f}, {tower_lng:.5f}'
    except (TypeError, ValueError):
        coordinates = '—'

    photos = TowerPhoto.query.filter_by(line_id=line_id, tower_label=tower_label).all()
    photo_ids = [p.id for p in photos]
    defects = []
    if photo_ids:
        defects = (TowerDefect.query
                   .filter(TowerDefect.tower_photo_id.in_(photo_ids))
                   .order_by(TowerDefect.created_at.asc()).all())

    photo_by_id = {p.id: p for p in photos}
    defect_dicts = []
    for d in defects:
        photo = photo_by_id.get(d.tower_photo_id)
        entry = d.to_dict()
        entry['image_path'] = photo.image_path if photo else ''
        defect_dicts.append(entry)

    info = {
        'line_name': line.name,
        'tower_id': tower_label,
        'voltage_level': line.voltage_level or '',
        'coordinates': coordinates,
        'survey_date': line.survey_date.strftime('%d %b %Y') if line.survey_date else '',
        'pilot_name': line.pilot_name or '',
        'inspection_name': line.inspection_name or '',
        'report_date': datetime.utcnow().strftime('%d %b %Y'),
    }

    try:
        from tower_report import build_tower_report_pdf
        static_root = os.path.join(current_app.root_path, 'static')
        pdf_buf = build_tower_report_pdf(info, defect_dicts, static_root)
    except Exception as e:
        current_app.logger.exception('Tower report generation failed for line %s tower %s', line_id, tower_label)
        return jsonify({'error': f'Report generation failed: {e}'}), 500

    # Save to disk under static/uploads/tower_reports/, same pattern as
    # _save_upload() but for a PDF we built ourselves rather than an
    # uploaded file.
    folder_fs = os.path.join(current_app.root_path, UPLOAD_BASE, 'tower_reports')
    os.makedirs(folder_fs, exist_ok=True)
    safe_tower = secure_filename(tower_label) or 'tower'
    filename = f'line{line_id}_{safe_tower}_report.pdf'
    full_path = os.path.join(folder_fs, filename)
    with open(full_path, 'wb') as f:
        f.write(pdf_buf.getvalue())
    report_path = f'uploads/tower_reports/{filename}'

    # Replace any existing report for this exact tower rather than
    # accumulating duplicate rows/files each time it's regenerated.
    existing = TowerReport.query.filter_by(line_id=line_id, tower_label=tower_label).first()
    if existing:
        existing.report_path = report_path
        existing.generated_by = session.get('user_name', '')
        existing.generated_at = datetime.utcnow()
        report = existing
    else:
        report = TowerReport(
            line_id=line_id, tower_label=tower_label, report_path=report_path,
            generated_by=session.get('user_name', ''),
        )
        db.session.add(report)
    db.session.commit()
    return jsonify(report.to_dict()), 201


@projects_bp.route('/api/tower-reports/<int:report_id>/download', methods=['GET'])
def api_download_tower_report(report_id):
    guard = _login_guard()
    if guard:
        return guard
    report = TowerReport.query.get_or_404(report_id)
    full_path = os.path.join(current_app.root_path, 'static', report.report_path)
    if not os.path.exists(full_path):
        return jsonify({'error': 'Report file not found — try generating it again.'}), 404
    from flask import send_file
    download_name = f'Tower_{secure_filename(report.tower_label)}_Inspection_Report.pdf'
    return send_file(full_path, as_attachment=True, download_name=download_name, mimetype='application/pdf')
