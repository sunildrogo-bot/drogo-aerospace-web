"""
settings.py — App-wide settings helpers (currently just the shared
"delete password" used to confirm project/chimney deletions).
"""
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, AppSetting

DELETE_PASSWORD_KEY = 'delete_password_hash'


def has_delete_password() -> bool:
    return AppSetting.query.get(DELETE_PASSWORD_KEY) is not None


def set_delete_password(new_password: str) -> None:
    row = AppSetting.query.get(DELETE_PASSWORD_KEY)
    hashed = generate_password_hash(new_password)
    if row:
        row.value = hashed
    else:
        row = AppSetting(key=DELETE_PASSWORD_KEY, value=hashed)
        db.session.add(row)
    db.session.commit()


def verify_delete_password(candidate: str) -> bool:
    """Returns True if candidate matches the stored delete password.
    If no delete password has been set yet, deletion is blocked (returns
    False) until an Admin sets one from Settings — safer default than
    allowing free deletion."""
    row = AppSetting.query.get(DELETE_PASSWORD_KEY)
    if not row or not candidate:
        return False
    return check_password_hash(row.value, candidate)
