"""
users.py — All user CRUD backed by SQLAlchemy (SQLite by default).
The rest of the app (app.py, users_routes.py) keeps the SAME call signatures
as before — only this file changed.
"""
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, Role, Module

# ── Constants (same as before) ────────────────────────────────────────────────
ROLES    = ['Admin', 'Client User', 'Pilot']
MODULES  = ['Transmission Line', 'Land Survey', '3D Inspection', 'TRANS']
STATUSES = ['Active', 'Inactive', 'Pending']

MODULE_ROUTES = {
    'Transmission Line':  'projects',
    'Land Survey':        'land_survey_dashboard',
    '3D Inspection': 'chimney_bp.chimney_dashboard',
    'TRANS': 'trans_dashboard',
}

# Cover image + accent colour shown on each module card (client dashboard
# and Admin's Module Management grid use the same set, so the two stay in
# sync). Keys must match MODULES above exactly.
MODULE_CARDS = {
    'Transmission Line': {
        'image': 'https://images.pexels.com/photos/611219/watt-electricity-sky-power-611219.jpeg?auto=compress&cs=tinysrgb&w=900',
        'accent': '#b7caf1',
        'desc': 'Monitor high-voltage transmission corridors with real-time tower health data.',
    },
    'Land Survey': {
        'image': 'https://images.pexels.com/photos/3030296/pexels-photo-3030296.jpeg?auto=compress&cs=tinysrgb&w=900',
        'accent': '#bfdbe8',
        'desc': 'Aerial drone surveys for right-of-way mapping and encroachment detection.',
    },
    '3D Inspection': {
        'image': 'https://images.pexels.com/photos/247763/pexels-photo-247763.jpeg?auto=compress&cs=tinysrgb&w=900',
        'accent': '#c9cdd6',
        'desc': 'Drone-based 3D scan inspection of industrial structures — chimneys, water tanks and more.',
    },
    'TRANS': {
        'image': 'https://images.pexels.com/photos/611219/watt-electricity-sky-power-611219.jpeg?auto=compress&cs=tinysrgb&w=900',
        'accent': '#b7caf1',
        'desc': 'Transmission line project tracking — divisions, towers, and line mapping.',
    },
}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_or_create_role(name: str) -> Role:
    with db.session.no_autoflush:
        role = Role.query.filter_by(name=name).first()
        if not role:
            role = Role(name=name)
            db.session.add(role)
            db.session.flush()
    return role


def _get_or_create_module(name: str) -> Module:
    with db.session.no_autoflush:
        mod = Module.query.filter_by(name=name).first()
        if not mod:
            mod = Module(name=name, route=MODULE_ROUTES.get(name, ''))
            db.session.add(mod)
            db.session.flush()
    return mod


def _build_query(search='', role='', module='', status=''):
    q = User.query
    if search:
        like = f'%{search}%'
        q = q.filter(
            db.or_(
                User.username.ilike(like),
                User.email.ilike(like),
            )
        )
    if role:
        q = q.join(User.roles).filter(Role.name == role)
    if module:
        q = q.join(User.modules).filter(Module.name == module)
    if status:
        q = q.filter(User.status == status)
    return q


# ── Public CRUD ───────────────────────────────────────────────────────────────

def get_all(search='', role='', module='', status='') -> list:
    # Pending is a real stored value so it can filter at the SQL level;
    # Active/Inactive are computed from login recency (see
    # User.effective_status()), so those are filtered in Python after
    # fetching — otherwise the filter dropdown could disagree with what's
    # actually shown in the table.
    sql_status = status if status == 'Pending' else ''
    users = _build_query(search, role, module, sql_status).all()
    if status in ('Active', 'Inactive'):
        users = [u for u in users if u.effective_status() == status]
    return [u.to_dict() for u in users]


def get_by_id(uid, include_password: bool = False):
    user = User.query.get(int(uid))
    if not user:
        return None
    return user.to_dict(include_password=include_password)


def get_by_email(email: str):
    return User.query.filter(User.email.ilike(email.strip())).first()


def authenticate(email: str, password: str, login_as=None):
    """
    Returns (user_dict, error_str).
    user_dict contains roles & modules lists (no password hash).
    """
    user = get_by_email(email)
    if not user:
        return None, 'Invalid email or password.'
    if not check_password_hash(user.password_hash, password or ''):
        return None, 'Invalid email or password.'
    if user.status != 'Active':
        return None, 'This account is not active. Contact your administrator.'
    if login_as and login_as not in user.role_names():
        return None, f"This account doesn't have {login_as} access."
    return user.to_dict(), None


def record_login(uid) -> None:
    user = User.query.get(int(uid))
    if user:
        user.last_login = datetime.now().strftime('%d %b %Y, %I:%M %p')
        user.last_login_at = datetime.utcnow()
        db.session.commit()


def create(data: dict) -> dict:
    raw_password = data.get('password') or _generate_password()

    user = User(
        username      = data.get('username', '').strip(),
        email         = data.get('email', '').strip().lower(),
        password_hash = generate_password_hash(raw_password),
        contact       = data.get('contact', '').strip(),
        photo_path    = data.get('photo_path', '') or '',
        status        = data.get('status', 'Pending'),
    )

    for role_name in (data.get('roles') or ['Client User']):
        user.roles.append(_get_or_create_role(role_name))

    for mod_name in (data.get('modules') or []):
        user.modules.append(_get_or_create_module(mod_name))

    _apply_project_restrictions(user, data)

    db.session.add(user)
    db.session.commit()

    result = user.to_dict()
    result['_generated_password'] = raw_password
    return result


def _apply_project_restrictions(user, data):
    """Sets which specific projects a user is restricted to, for modules
    where that's been configured. Leaving a module's project list empty
    means "full access to every project in that module" — see
    User.restricted_project_ids_for_module()'s docstring."""
    from models import Project, ChimneyProject
    if 'project_ids' in data and isinstance(data['project_ids'], list):
        ids = [int(i) for i in data['project_ids'] if str(i).isdigit()]
        user.allowed_projects = Project.query.filter(Project.id.in_(ids)).all() if ids else []
    if 'chimney_project_ids' in data and isinstance(data['chimney_project_ids'], list):
        ids = [int(i) for i in data['chimney_project_ids'] if str(i).isdigit()]
        user.allowed_chimney_projects = ChimneyProject.query.filter(ChimneyProject.id.in_(ids)).all() if ids else []


def update(uid, data: dict):
    user = User.query.get(int(uid))
    if not user:
        return None

    if data.get('username'):
        user.username = data['username'].strip()
    if data.get('email'):
        user.email = data['email'].strip().lower()
    if 'contact' in data:
        user.contact = data['contact'].strip()
    if data.get('photo_path'):
        user.photo_path = data['photo_path']
    if data.get('status'):
        user.status = data['status']
    if data.get('password'):
        user.password_hash = generate_password_hash(data['password'])

    if 'roles' in data and isinstance(data['roles'], list):
        user.roles = [_get_or_create_role(r) for r in data['roles']]

    if 'modules' in data and isinstance(data['modules'], list):
        user.modules = [_get_or_create_module(m) for m in data['modules']]

    _apply_project_restrictions(user, data)

    db.session.commit()
    return user.to_dict()


def delete(uid) -> bool:
    user = User.query.get(int(uid))
    if not user:
        return False
    db.session.delete(user)
    db.session.commit()
    return True


def _generate_password(length: int = 7) -> str:
    """
    Generates a 7-character system password containing a mix of
    lowercase, uppercase, and a minimal set of special characters.
    Guarantees at least one of each category, then fills the rest randomly.
    Special character set is kept minimal (no quotes/backslashes/spaces)
    to avoid issues with copy/paste, URLs, or shell/SQL edge cases.
    """
    import secrets, string
    lower   = string.ascii_lowercase
    upper   = string.ascii_uppercase
    special = '!@#$%'  # minimal, unambiguous special characters

    # Guarantee at least one char from each required category
    required = [
        secrets.choice(lower),
        secrets.choice(upper),
        secrets.choice(special),
    ]

    # Fill the remaining length from the combined pool (letters-heavy)
    pool = lower + upper + special
    remaining = [secrets.choice(pool) for _ in range(length - len(required))]

    pwd_chars = required + remaining
    # Shuffle so the special char / uppercase aren't always in fixed positions
    for i in range(len(pwd_chars) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        pwd_chars[i], pwd_chars[j] = pwd_chars[j], pwd_chars[i]

    return ''.join(pwd_chars)
