"""
announcement_routes.py — Admin-posted announcements, shown as a bell
popup on the home page.

Replaces the earlier help_routes.py ticket system: this is one-way
(Admin -> everyone) rather than users raising individual problems.

Register in app.py with: app.register_blueprint(announcement_bp)
"""
import os
from flask import Blueprint, request, jsonify, session, redirect, url_for, current_app

from models import db, Announcement

announcement_bp = Blueprint('announcement_bp', __name__)

IMG_SUBDIR = os.path.join('uploads', 'announcement_images')  # under /static


def _login_guard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return None


def _admin_guard():
    guard = _login_guard()
    if guard:
        return guard
    if session.get('role') != 'Admin':
        return jsonify({'error': 'Admin access required.'}), 403
    return None


@announcement_bp.route('/api/announcements', methods=['GET'])
def api_list_announcements():
    guard = _login_guard()
    if guard:
        return guard
    entries = Announcement.query.order_by(Announcement.created_at.desc()).limit(30).all()
    return jsonify({'announcements': [a.to_dict() for a in entries]})


@announcement_bp.route('/api/announcements', methods=['POST'])
def api_create_announcement():
    guard = _admin_guard()
    if guard:
        return guard

    title = (request.form.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'Please give the announcement a title.'}), 400

    ann = Announcement(
        title=title,
        message=(request.form.get('message') or '').strip(),
        created_by=session.get('user_name', ''),
    )
    db.session.add(ann)
    db.session.flush()  # get ann.id before saving the image file

    file = request.files.get('image')
    if file and file.filename:
        dest_dir = os.path.join(current_app.root_path, 'static', IMG_SUBDIR)
        os.makedirs(dest_dir, exist_ok=True)
        ext = os.path.splitext(file.filename)[1] or '.jpg'
        filename = f'announcement_{ann.id}{ext}'
        dest_path = os.path.join(dest_dir, filename)
        file.save(dest_path)
        ann.image_path = os.path.relpath(dest_path, os.path.join(current_app.root_path, 'static')).replace(os.sep, '/')

    db.session.commit()
    return jsonify(ann.to_dict()), 201


@announcement_bp.route('/api/announcements/<int:ann_id>', methods=['DELETE'])
def api_delete_announcement(ann_id):
    guard = _admin_guard()
    if guard:
        return guard
    ann = Announcement.query.get_or_404(ann_id)
    if ann.image_path:
        img_path = os.path.join(current_app.root_path, 'static', ann.image_path)
        if os.path.exists(img_path):
            try:
                os.remove(img_path)
            except OSError:
                pass
    db.session.delete(ann)
    db.session.commit()
    return jsonify({'deleted': ann_id})
