"""
help_routes.py — Help tickets (Settings -> Help). Clients/Pilots/Admin raise
a problem; Admin works it through Checking -> Resolved. The person who
raised it gets notified (seen_by_reporter flips False on any status
change) until they view it again.

Register in app.py with: app.register_blueprint(help_bp)
"""
from flask import Blueprint, request, jsonify, session, redirect, url_for

from models import db, HelpTicket

help_bp = Blueprint('help_bp', __name__)


def _login_guard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return None


@help_bp.route('/api/help-tickets', methods=['GET'])
def api_list_help_tickets():
    guard = _login_guard()
    if guard:
        return guard
    q = HelpTicket.query.order_by(HelpTicket.created_at.desc())
    if session.get('role') != 'Admin':
        q = q.filter_by(submitted_by=session.get('user_name', ''))
    tickets = q.limit(100).all()
    return jsonify({'tickets': [t.to_dict() for t in tickets]})


@help_bp.route('/api/help-tickets', methods=['POST'])
def api_create_help_ticket():
    guard = _login_guard()
    if guard:
        return guard
    data = request.get_json(force=True, silent=True) or request.form

    subject = (data.get('subject') or '').strip()
    if not subject:
        return jsonify({'error': 'Please describe the problem in a few words (subject).'}), 400

    reporter_type = (data.get('reporter_type') or '').strip()
    if reporter_type not in ('Client', 'Pilot', 'Admin'):
        reporter_type = 'Admin' if session.get('role') == 'Admin' else 'Client'

    ticket = HelpTicket(
        subject=subject,
        description=(data.get('description') or '').strip(),
        reporter_type=reporter_type,
        submitted_by=session.get('user_name', ''),
        status='Open',
        seen_by_reporter=True,
    )
    db.session.add(ticket)
    db.session.commit()
    return jsonify(ticket.to_dict()), 201


@help_bp.route('/api/help-tickets/<int:ticket_id>', methods=['PATCH'])
def api_update_help_ticket(ticket_id):
    guard = _login_guard()
    if guard:
        return guard

    ticket = HelpTicket.query.get_or_404(ticket_id)
    data = request.get_json(force=True, silent=True) or {}

    # Admin moves it through Checking / Resolved — flips seen_by_reporter
    # off so the raiser sees it's changed next time they check.
    if 'status' in data:
        if session.get('role') != 'Admin':
            return jsonify({'error': 'Only Admin can update ticket status.'}), 403
        status = (data.get('status') or '').strip()
        if status not in HelpTicket.STATUSES:
            return jsonify({'error': f"status must be one of {HelpTicket.STATUSES}."}), 400
        ticket.status = status
        ticket.seen_by_reporter = False
        if status == 'Resolved':
            ticket.resolved_by = session.get('user_name', '')

    # The raiser marks it seen once they've checked the update — anyone
    # can mark their own ticket seen, not just Admin.
    if data.get('mark_seen'):
        if ticket.submitted_by == session.get('user_name', '') or session.get('role') == 'Admin':
            ticket.seen_by_reporter = True

    db.session.commit()
    return jsonify(ticket.to_dict())
