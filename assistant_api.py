"""
assistant_api.py — In-app AI chat assistant.

Runs as a tool-calling agent against Google's Gemini API (free tier —
switched from Anthropic's paid API by request). Every tool function below
queries the database using the SAME permission-filtering functions the
rest of the app already uses (_visible_project_ids /
_visible_chimney_project_ids, imported directly from projects_routes.py
and chimney_routes.py rather than reimplemented here) — so a Client User
with restricted project access gets those same restrictions applied to
whatever the assistant can see and do on their behalf, by construction,
not by a prompt instruction that could be talked around.

Honest scope: this is a text chat assistant. It can look up and explain
data, and trigger the same "generate a report" / "create a division"
actions the UI already exposes as simple form-fills. It CANNOT draw a
defect shape on a photo (a spatial/mouse action) or receive an uploaded
file as part of a chat message — those still need the regular UI, and the
system prompt tells the assistant to say so plainly rather than pretend.

Note on the free tier: Gemini's free tier trains on submitted data by
default (no paid-tier privacy guarantee behind it) — worth knowing given
this handles real inspection/defect data. See the conversation this was
built in for the tradeoff discussion.
"""
import os
import json
from datetime import datetime
from flask import Blueprint, request, jsonify, session

from models import db, Project, Division, Line, TowerPhoto, TowerDefect, TowerReport, ChimneyProject, ChimneyDefect, User, HelpTicket

assistant_bp = Blueprint('assistant_bp', __name__, url_prefix='/api/assistant')

MODEL = 'gemini-3.1-flash-lite'  # switched from gemini-3.5-flash — that model's free tier is only 20 requests/DAY, which gets exhausted almost immediately with real testing. gemini-3.1-flash-lite (the stable release, not the retired -preview variant) gets 1,500/day free — same general capability class (tool-calling works the same way), just a far more usable free-tier quota. gemini-2.5-flash is deprecated for new users (404) as of Jul 2026; avoid '-latest' aliases and gemini-3.6-flash (released Jul 21 2026, still rolling out) for something this needs to keep working reliably.
MAX_TOOL_ROUNDS = 6  # hard cap on tool-call round-trips per message, so a confused loop can't run forever


def _login_guard():
    if 'user_id' not in session:
        return jsonify({'error': 'Not logged in.'}), 401
    return None


def _is_admin():
    return session.get('role') == 'Admin'


def _current_user():
    uid = session.get('user_id')
    return User.query.get(uid) if uid else None


# ── Tool implementations ─────────────────────────────────────────────────
# Every function here takes/returns plain JSON-serializable data (this is
# what gets sent back to Claude as the tool result) and is scoped to what
# the CURRENT session is actually allowed to see, using the app's real
# permission logic rather than anything reimplemented for the assistant.

def _visible_trans_project_ids(module_name):
    from projects_routes import _visible_project_ids
    return _visible_project_ids(module_name)


def _visible_chimney_ids():
    from chimney_routes import _visible_chimney_project_ids
    return _visible_chimney_project_ids()


def tool_list_projects(module=None):
    modules = [module] if module else ['Transmission Line', 'Land Survey', 'TRANS']
    out = []
    for m in modules:
        rows = Project.query.filter_by(module=m).all()
        allowed = _visible_trans_project_ids(m)
        if allowed is not None:
            rows = [r for r in rows if r.id in allowed]
        out.extend([{'id': r.id, 'name': r.name, 'module': r.module, 'state': r.state,
                      'client_name': r.client_name} for r in rows])
    return {'projects': out}


def tool_list_chimney_projects():
    rows = ChimneyProject.query.all()
    allowed = _visible_chimney_ids()
    if allowed is not None:
        rows = [r for r in rows if r.id in allowed]
    return {'projects': [{'id': r.id, 'name': r.asset_name, 'category': r.asset_category} for r in rows]}


def _find_project(project_name):
    rows = Project.query.filter(Project.name.ilike(f'%{project_name}%')).all()
    if not rows:
        return None, {'error': f'No project found matching "{project_name}".'}
    if len(rows) > 1:
        return None, {'error': 'Multiple projects match that name — please be more specific.',
                       'matches': [r.name for r in rows]}
    project = rows[0]
    allowed = _visible_trans_project_ids(project.module)
    if allowed is not None and project.id not in allowed:
        return None, {'error': "You don't have access to this project."}
    return project, None


def tool_get_project_overview(project_name):
    project, err = _find_project(project_name)
    if err:
        return err
    from projects_routes import _build_project_defect_summary
    s = _build_project_defect_summary(project)
    return {
        'project': project.name, 'module': project.module,
        'divisions': s['division_count'], 'lines': s['line_count'],
        'towers': s['tower_count'], 'towers_photographed': s['towers_photographed'],
        'total_defects': s['total_defects'], 'severity_counts': s['severity_counts'],
        'defects_by_division': s['division_defect_counts'],
    }


def tool_list_divisions(project_name):
    project, err = _find_project(project_name)
    if err:
        return err
    return {'divisions': [{'id': d.id, 'name': d.name, 'state': d.state, 'lines': len(d.lines)}
                           for d in project.divisions]}


def tool_list_lines(project_name, division_name=None):
    project, err = _find_project(project_name)
    if err:
        return err
    divisions = project.divisions
    if division_name:
        divisions = [d for d in divisions if division_name.lower() in d.name.lower()]
        if not divisions:
            return {'error': f'No division found matching "{division_name}" in this project.'}
    out = []
    for d in divisions:
        for l in d.lines:
            out.append({'id': l.id, 'name': l.name, 'division': d.name, 'tower_count': l.tower_count,
                        'voltage_level': l.voltage_level, 'pilot_name': l.pilot_name})
    return {'lines': out}


def _find_line(project_name, line_name):
    project, err = _find_project(project_name)
    if err:
        return None, err
    matches = [l for d in project.divisions for l in d.lines if line_name.lower() in l.name.lower()]
    if not matches:
        return None, {'error': f'No line found matching "{line_name}" in project "{project.name}".'}
    if len(matches) > 1:
        return None, {'error': 'Multiple lines match that name — please be more specific.',
                       'matches': [l.name for l in matches]}
    return matches[0], None


def tool_list_tower_defects(project_name, line_name, tower_label):
    line, err = _find_line(project_name, line_name)
    if err:
        return err
    photos = TowerPhoto.query.filter_by(line_id=line.id, tower_label=str(tower_label)).all()
    photo_ids = [p.id for p in photos]
    if not photo_ids:
        return {'defects': [], 'note': 'No photos uploaded for this tower yet.'}
    defects = TowerDefect.query.filter(TowerDefect.tower_photo_id.in_(photo_ids)).all()
    return {'defects': [{
        'id': d.id, 'component_name': d.component_name, 'defect_type': d.defect_type,
        'location': d.location, 'severity': d.severity, 'status': d.status,
        'observation': d.observation, 'created_at': d.created_at.strftime('%d %b %Y') if d.created_at else '',
    } for d in defects]}


def tool_search_defects(project_name, severity=None, status=None, component_name=None):
    project, err = _find_project(project_name)
    if err:
        return err
    line_ids = [l.id for d in project.divisions for l in d.lines]
    if not line_ids:
        return {'defects': []}
    q = (TowerDefect.query.join(TowerPhoto, TowerDefect.tower_photo_id == TowerPhoto.id)
         .filter(TowerPhoto.line_id.in_(line_ids)))
    if severity:
        q = q.filter(TowerDefect.severity == severity)
    if status:
        q = q.filter(TowerDefect.status == status)
    if component_name:
        q = q.filter(TowerDefect.component_name.ilike(f'%{component_name}%'))
    defects = q.order_by(TowerDefect.created_at.desc()).limit(50).all()
    return {'defects': [{
        'id': d.id, 'tower': d.photo.tower_label if d.photo else '?', 'component_name': d.component_name,
        'defect_type': d.defect_type, 'severity': d.severity, 'status': d.status,
        'observation': d.observation,
    } for d in defects], 'note': 'Capped at 50 most recent matches.'}


def tool_generate_tower_report(project_name, line_name, tower_label):
    if not _is_admin():
        return {'error': 'Only Admin accounts can generate reports.'}
    line, err = _find_line(project_name, line_name)
    if err:
        return err

    from models import TowerInspectionStatus
    status = TowerInspectionStatus.query.filter_by(line_id=line.id, tower_label=str(tower_label)).first()
    if not status or not status.inspection_done:
        return {'error': 'This tower has not been marked "Inspection Done" yet — mark it done before generating a report.'}

    # Duplicates the core of api_generate_tower_report() in projects_routes.py
    # rather than calling it directly, since that route reads its inputs from
    # a Flask request object this tool doesn't have — same underlying report
    # builder and storage logic either way.
    from flask import current_app
    import projects_routes as pr
    photos = TowerPhoto.query.filter_by(line_id=line.id, tower_label=str(tower_label)).all()
    photo_ids = [p.id for p in photos]
    defects = TowerDefect.query.filter(TowerDefect.tower_photo_id.in_(photo_ids)).all() if photo_ids else []
    photo_by_id = {p.id: p for p in photos}
    defect_dicts = []
    for d in defects:
        photo = photo_by_id.get(d.tower_photo_id)
        entry = d.to_dict()
        entry['image_path'] = photo.image_path if photo else ''
        defect_dicts.append(entry)
    info = {
        'line_name': line.name, 'tower_id': str(tower_label), 'voltage_level': line.voltage_level or '',
        'coordinates': '—', 'survey_date': line.survey_date.strftime('%d %b %Y') if line.survey_date else '',
        'pilot_name': line.pilot_name or '', 'inspection_name': line.inspection_name or '',
        'report_date': datetime.utcnow().strftime('%d %b %Y'),
    }
    from tower_report import build_tower_report_pdf
    static_root = os.path.join(current_app.root_path, 'static')
    pdf_buf = build_tower_report_pdf(info, defect_dicts, static_root)
    folder_fs = os.path.join(current_app.root_path, pr.UPLOAD_BASE, 'tower_reports')
    os.makedirs(folder_fs, exist_ok=True)
    from werkzeug.utils import secure_filename
    safe_tower = secure_filename(str(tower_label)) or 'tower'
    filename = f'line{line.id}_{safe_tower}_report.pdf'
    full_path = os.path.join(folder_fs, filename)
    with open(full_path, 'wb') as f:
        f.write(pdf_buf.getvalue())
    report_path = f'uploads/tower_reports/{filename}'
    existing = TowerReport.query.filter_by(line_id=line.id, tower_label=str(tower_label)).first()
    if existing:
        existing.report_path = report_path
        existing.generated_by = session.get('user_name', '')
        existing.generated_at = datetime.utcnow()
        report = existing
    else:
        report = TowerReport(line_id=line.id, tower_label=str(tower_label), report_path=report_path,
                              generated_by=session.get('user_name', ''))
        db.session.add(report)
    db.session.commit()
    return {'ok': True, 'download_url': f'/api/tower-reports/{report.id}/download',
            'defect_count': len(defect_dicts)}


TOOLS = [
    {'name': 'list_projects', 'description': 'List Transmission Line / Land Survey / TRANS projects the current user can see. Optionally filter by module name.',
     'parameters': {'type': 'object', 'properties': {'module': {'type': 'string', 'description': "One of 'Transmission Line', 'Land Survey', 'TRANS' — omit to list all"}}}},
    {'name': 'list_chimney_projects', 'description': 'List 3D Inspection (chimney/water tank) projects the current user can see.',
     'parameters': {'type': 'object', 'properties': {}}},
    {'name': 'get_project_overview', 'description': 'Get division/line/tower/defect counts and severity breakdown for a Transmission Line / Land Survey / TRANS project by name.',
     'parameters': {'type': 'object', 'properties': {'project_name': {'type': 'string'}}, 'required': ['project_name']}},
    {'name': 'list_divisions', 'description': "List a project's divisions.",
     'parameters': {'type': 'object', 'properties': {'project_name': {'type': 'string'}}, 'required': ['project_name']}},
    {'name': 'list_lines', 'description': "List lines in a project, optionally within one division.",
     'parameters': {'type': 'object', 'properties': {'project_name': {'type': 'string'}, 'division_name': {'type': 'string'}}, 'required': ['project_name']}},
    {'name': 'list_tower_defects', 'description': 'List all defects marked on a specific tower.',
     'parameters': {'type': 'object', 'properties': {'project_name': {'type': 'string'}, 'line_name': {'type': 'string'}, 'tower_label': {'type': 'string'}}, 'required': ['project_name', 'line_name', 'tower_label']}},
    {'name': 'search_defects', 'description': 'Search defects across a whole project by severity, status, and/or component name.',
     'parameters': {'type': 'object', 'properties': {
         'project_name': {'type': 'string'},
         'severity': {'type': 'string', 'enum': ['Minor', 'Major', 'Critical']},
         'status': {'type': 'string', 'enum': ['Open', 'Closed']},
         'component_name': {'type': 'string'},
     }, 'required': ['project_name']}},
    {'name': 'generate_tower_report', 'description': 'Generate (or regenerate) the RGB Visual Inspection PDF report for a specific tower. Admin only.',
     'parameters': {'type': 'object', 'properties': {'project_name': {'type': 'string'}, 'line_name': {'type': 'string'}, 'tower_label': {'type': 'string'}}, 'required': ['project_name', 'line_name', 'tower_label']}},
    {'name': 'generate_central_report', 'description': 'Generate ONE combined PDF report covering every project the user can see, across every module (Transmission Line, Land Survey, TRANS, and 3D Inspection).',
     'parameters': {'type': 'object', 'properties': {}}},
    {'name': 'create_help_ticket', 'description': 'Raise a support ticket about a portal problem (a bug, something not working, a question for the team) — shows up for Admin in Settings > Help.',
     'parameters': {'type': 'object', 'properties': {
         'subject': {'type': 'string', 'description': 'A short summary of the problem, a few words'},
         'description': {'type': 'string', 'description': 'More detail about what happened, optional'},
     }, 'required': ['subject']}},
]

def tool_generate_central_report():
    """One combined PDF across every project the current user can see,
    across every module — gathers the same per-project summary shape
    already used elsewhere (trans-style projects via
    _build_project_defect_summary, chimney projects via a lightweight
    open/closed/severity rollup) and hands them to central_report.py."""
    out = []

    for module_name in ('Transmission Line', 'Land Survey', 'TRANS'):
        rows = Project.query.filter_by(module=module_name).all()
        allowed = _visible_trans_project_ids(module_name)
        if allowed is not None:
            rows = [r for r in rows if r.id in allowed]
        from projects_routes import _build_project_defect_summary
        for project in rows:
            s = _build_project_defect_summary(project)
            out.append({
                'name': project.name, 'module': module_name,
                'division_count': s['division_count'], 'line_count': s['line_count'],
                'tower_count': s['tower_count'], 'total_defects': s['total_defects'],
                'severity_counts': s['severity_counts'],
            })

    chimney_rows = ChimneyProject.query.all()
    allowed_chimney = _visible_chimney_ids()
    if allowed_chimney is not None:
        chimney_rows = [r for r in chimney_rows if r.id in allowed_chimney]
    for project in chimney_rows:
        defects = project.defects
        open_count = sum(1 for d in defects if d.status != 'Closed')
        closed_count = sum(1 for d in defects if d.status == 'Closed')
        severity_counts = {'Minor': 0, 'Moderate': 0, 'Critical': 0}
        for d in defects:
            if d.severity in severity_counts:
                severity_counts[d.severity] += 1
        out.append({
            'name': project.asset_name, 'module': '3D Inspection',
            'total_defects': len(defects), 'open_count': open_count, 'closed_count': closed_count,
            'severity_counts': severity_counts,
        })

    if not out:
        return {'error': "You don't have access to any projects to include in a report."}

    from central_report import build_central_report_pdf
    pdf_buf = build_central_report_pdf(out)

    from flask import current_app
    folder_fs = os.path.join(current_app.root_path, 'static', 'uploads', 'central_reports')
    os.makedirs(folder_fs, exist_ok=True)
    filename = f"central_report_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf"
    full_path = os.path.join(folder_fs, filename)
    with open(full_path, 'wb') as f:
        f.write(pdf_buf.getvalue())

    return {'ok': True, 'download_url': f'/static/uploads/central_reports/{filename}',
            'project_count': len(out), 'total_defects': sum(p['total_defects'] for p in out)}


def tool_create_help_ticket(subject, description=''):
    """Raises a support ticket about a portal issue — same underlying
    HelpTicket record the Settings -> Help admin view already lists and
    works through, so a ticket raised here shows up there with zero extra
    wiring needed."""
    if _is_admin():
        return {'error': "Admin accounts resolve tickets rather than raise their own — see Settings > Raised Tickets."}
    subject = (subject or '').strip()
    if not subject:
        return {'error': 'A short subject describing the problem is required.'}
    reporter_type = 'Admin' if _is_admin() else 'Client'
    ticket = HelpTicket(
        subject=subject,
        description=(description or '').strip(),
        reporter_type=reporter_type,
        submitted_by=session.get('user_name', ''),
        status='Open',
        seen_by_reporter=True,
    )
    db.session.add(ticket)
    db.session.commit()
    return {'ok': True, 'ticket_id': ticket.id, 'note': 'Ticket raised — the team will follow up.'}


TOOL_FUNCTIONS = {
    'list_projects': tool_list_projects,
    'list_chimney_projects': tool_list_chimney_projects,
    'get_project_overview': tool_get_project_overview,
    'list_divisions': tool_list_divisions,
    'list_lines': tool_list_lines,
    'list_tower_defects': tool_list_tower_defects,
    'search_defects': tool_search_defects,
    'generate_tower_report': tool_generate_tower_report,
    'generate_central_report': tool_generate_central_report,
    'create_help_ticket': tool_create_help_ticket,
}


def _system_prompt():
    user = _current_user()
    name = user.username if user else 'there'
    role = session.get('role', 'Client User')
    ticket_line = (
        "Admin accounts resolve tickets rather than raise their own (see Settings > Raised Tickets) — don't offer to raise one for an Admin."
        if _is_admin() else
        "You can also raise a support ticket on the person's behalf if they describe a portal problem — confirm the subject with them before creating it, and let them know it's been raised."
    )
    return f"""You are the in-app assistant for DROGO AEROSPACE's inspection platform, talking to {name} ({role}).

You can look up projects, divisions, lines, towers, and defects, and generate tower or central inspection reports (report generation is Admin only). {ticket_line} Every tool call is automatically scoped to exactly what this user is allowed to see — if something isn't accessible, the tool will say so; don't try to work around that or guess at data you don't have.

Be honest about what you can't do: you cannot draw a defect shape on a photo (that's a mouse/spatial action) and you cannot receive an uploaded file as part of this chat — for those, tell the person to use the regular photo/mark-defect screen. Don't imply you did something you didn't.

Keep answers concise and concrete — real numbers and names from tool results, not vague summaries. If a name is ambiguous, ask which one they mean rather than guessing."""


def _gemini_tools():
    tools = TOOLS
    if _is_admin():
        # Admin resolves tickets (Settings -> Raised Tickets), doesn't
        # raise their own — mirrors the UI, where the "Raise a Ticket"
        # button is hidden for Admin sessions. Excluded here too, not just
        # hidden in the UI, so asking in natural language can't get around it.
        tools = [t for t in TOOLS if t['name'] != 'create_help_ticket']
    return [{'function_declarations': [
        {'name': t['name'], 'description': t['description'], 'parameters': t['parameters']} for t in tools
    ]}]


@assistant_bp.route('/chat', methods=['POST'])
def chat():
    guard = _login_guard()
    if guard:
        return guard

    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        return jsonify({'error': 'The assistant is not configured yet — GEMINI_API_KEY is not set on the server.'}), 503

    data = request.get_json(force=True, silent=True) or {}
    user_message = (data.get('message') or '').strip()
    history = data.get('history') or []
    if not user_message:
        return jsonify({'error': 'message is required.'}), 400

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return jsonify({'error': 'The google-genai package is not installed on the server.'}), 503

    client = genai.Client(api_key=api_key)

    # History is kept client-side as plain JSON (what the frontend sends
    # back each turn) and reconstructed into typed Content objects here —
    # keeps the wire format simple and avoids the frontend needing to know
    # anything about the SDK's internal types.
    contents = [types.Content.model_validate(h) for h in history]
    contents.append(types.Content(role='user', parts=[types.Part(text=user_message)]))

    config = types.GenerateContentConfig(
        system_instruction=_system_prompt(),
        tools=_gemini_tools(),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )

    try:
        for _ in range(MAX_TOOL_ROUNDS):
            response = client.models.generate_content(model=MODEL, contents=contents, config=config)
            candidate = response.candidates[0] if response.candidates else None
            parts = candidate.content.parts if candidate and candidate.content else []
            function_calls = [p.function_call for p in parts if p.function_call]

            if not function_calls:
                final_text = ''.join(p.text for p in parts if p.text)
                contents.append(candidate.content)
                return jsonify({'reply': final_text, 'history': [c.model_dump(mode='json') for c in contents]})

            contents.append(candidate.content)
            response_parts = []
            for fc in function_calls:
                fn = TOOL_FUNCTIONS.get(fc.name)
                try:
                    result = fn(**(fc.args or {})) if fn else {'error': f'Unknown tool {fc.name}'}
                except Exception as e:
                    result = {'error': str(e)}
                response_parts.append(types.Part(function_response=types.FunctionResponse(
                    name=fc.name, response={'result': json.loads(json.dumps(result, default=str))}
                )))
            contents.append(types.Content(role='user', parts=response_parts))

        return jsonify({'reply': "I'm having trouble finishing that request — could you try rephrasing it?",
                         'history': [c.model_dump(mode='json') for c in contents]})
    except Exception as e:
        return jsonify({'error': f'Assistant error: {e}'}), 500
