"""
migrate_add_defect_columns.py

Adds the columns used by the updated chimney defect features (defect type /
area / height label / saved image path) to an EXISTING `chimney_defects`
table, without touching any existing data.

`ensure_defect_columns(app)` is called automatically every time the Flask
app starts (see app.py) — so this fixes itself even if nobody remembers to
run the CLI version below. It is idempotent and safe to run/import any
number of times: it only ever adds columns that are actually missing, and
never drops or alters existing ones.

Manual/CLI usage (only needed if you want to run it standalone, e.g. before
first deploying, or against a database the app itself isn't pointed at):
    python migrate_add_defect_columns.py
"""
import sys
from sqlalchemy import inspect, text

# column_name -> per-dialect SQL type
NEW_DEFECT_COLUMNS = {
    'defect_type':  {'mssql': 'NVARCHAR(60)',  'default': 'VARCHAR(60)'},
    'area':         {'mssql': 'NVARCHAR(60)',  'default': 'VARCHAR(60)'},
    'height_label': {'mssql': 'NVARCHAR(40)',  'default': 'VARCHAR(40)'},
    'image_path':   {'mssql': 'NVARCHAR(255)', 'default': 'VARCHAR(255)'},
    'location':     {'mssql': 'NVARCHAR(150)', 'default': 'VARCHAR(150)'},
    'status':               {'mssql': 'NVARCHAR(20)',  'default': 'VARCHAR(20)'},
    'rectified_image_path': {'mssql': 'NVARCHAR(255)', 'default': 'VARCHAR(255)'},
    'closed_at':            {'mssql': 'DATETIME',       'default': 'DATETIME'},
    'closed_by':            {'mssql': 'NVARCHAR(120)', 'default': 'VARCHAR(120)'},
    'cam_pos_x': {'mssql': 'FLOAT', 'default': 'FLOAT'},
    'cam_pos_y': {'mssql': 'FLOAT', 'default': 'FLOAT'},
    'cam_pos_z': {'mssql': 'FLOAT', 'default': 'FLOAT'},
    'cam_dir_x': {'mssql': 'FLOAT', 'default': 'FLOAT'},
    'cam_dir_y': {'mssql': 'FLOAT', 'default': 'FLOAT'},
    'cam_dir_z': {'mssql': 'FLOAT', 'default': 'FLOAT'},
    'cam_up_x':  {'mssql': 'FLOAT', 'default': 'FLOAT'},
    'cam_up_y':  {'mssql': 'FLOAT', 'default': 'FLOAT'},
    'cam_up_z':  {'mssql': 'FLOAT', 'default': 'FLOAT'},
    'rendered_positions': {'mssql': 'NVARCHAR(MAX)', 'default': 'TEXT'},
}

NEW_PROJECT_COLUMNS = {
    'model_center_lat': {'mssql': 'FLOAT', 'default': 'FLOAT'},
    'model_center_lng': {'mssql': 'FLOAT', 'default': 'FLOAT'},
    'inspection_scope': {'mssql': 'NVARCHAR(MAX)', 'default': 'TEXT'},
    'pilots':           {'mssql': 'NVARCHAR(MAX)', 'default': 'TEXT'},
    'target_completion_date': {'mssql': 'DATE', 'default': 'DATE'},
    'cover_snapshot_path': {'mssql': 'NVARCHAR(255)', 'default': 'VARCHAR(255)'},
    # Which kind of 3D Inspection asset this is (chimney / water_tank / …).
    # Existing rows get backfilled to 'chimney' right after the column is
    # added, below — every project created before this existed WAS a
    # chimney, so this keeps them behaving exactly as before.
    'asset_category':    {'mssql': "NVARCHAR(30)", 'default': 'VARCHAR(30)'},
    'structure_height_m': {'mssql': 'FLOAT', 'default': 'FLOAT'},
}

NEW_HELP_TICKET_COLUMNS = {
    'seen_by_reporter': {'mssql': 'BIT', 'default': 'BOOLEAN'},
}

NEW_ACTIVITY_LOG_COLUMNS = {
    'role':             {'mssql': 'NVARCHAR(20)', 'default': 'VARCHAR(20)'},
    'duration_seconds': {'mssql': 'INT',          'default': 'INTEGER'},
}

NEW_USER_COLUMNS = {
    'last_login_at': {'mssql': 'DATETIME', 'default': 'DATETIME'},
    'photo_path':    {'mssql': 'NVARCHAR(255)', 'default': 'VARCHAR(255)'},
    'dashboard_notes': {'mssql': 'NVARCHAR(MAX)', 'default': 'TEXT'},
}

# For the generic 'projects' table (Transmission Line / Land Survey / TRANS /
# any future module) — distinct from NEW_PROJECT_COLUMNS above, which despite
# its name targets 'chimney_projects'.
NEW_GENERIC_PROJECT_COLUMNS = {
    'client_name':       {'mssql': 'NVARCHAR(150)', 'default': 'VARCHAR(150)'},
    'planned_divisions': {'mssql': 'INT',            'default': 'INTEGER'},
    'planned_towers':    {'mssql': 'INT',            'default': 'INTEGER'},
    'timeline':          {'mssql': 'NVARCHAR(150)',  'default': 'VARCHAR(150)'},
}

NEW_DIVISION_COLUMNS = {
    'client_name':    {'mssql': 'NVARCHAR(150)', 'default': 'VARCHAR(150)'},
    'state':          {'mssql': 'NVARCHAR(100)', 'default': 'VARCHAR(100)'},
    'planned_towers': {'mssql': 'INT',            'default': 'INTEGER'},
}

NEW_LINE_COLUMNS = {
    'voltage_level':   {'mssql': 'NVARCHAR(50)',  'default': 'VARCHAR(50)'},
    'survey_date':     {'mssql': 'DATE',          'default': 'DATE'},
    'pilot_name':      {'mssql': 'NVARCHAR(150)', 'default': 'VARCHAR(150)'},
    'inspection_name': {'mssql': 'NVARCHAR(150)', 'default': 'VARCHAR(150)'},
}

NEW_TOWER_DEFECT_COLUMNS = {
    'defect_type': {'mssql': 'NVARCHAR(100)', 'default': 'VARCHAR(100)'},
    'status':      {'mssql': 'NVARCHAR(20)',  'default': 'VARCHAR(20)'},
}

# Kept for backwards compatibility with any code importing the old name.
NEW_COLUMNS = NEW_DEFECT_COLUMNS


def _ensure_columns(app, table_name, new_columns, verbose=True):
    """Idempotently add any missing `new_columns` to `table_name` on
    whatever database `app` is currently configured to use. Never raises —
    logs and returns False on failure (e.g. insufficient DB permissions) so
    it can never block app startup."""
    from models import db

    def log(msg):
        if verbose:
            print(f"[migrate_add_defect_columns] {msg}")

    try:
        with app.app_context():
            engine = db.engine
            dialect = engine.dialect.name
            inspector = inspect(engine)

            if table_name not in inspector.get_table_names():
                log(f"No '{table_name}' table yet — it will be created "
                    "with all current columns automatically.")
                return True

            existing_cols = {c['name'] for c in inspector.get_columns(table_name)}
            missing = {name: spec for name, spec in new_columns.items() if name not in existing_cols}

            if not missing:
                log(f"{table_name} schema already up to date.")
                return True

            log(f"Database dialect: {dialect}. Adding missing column(s) to {table_name}: {list(missing.keys())}")
            with engine.begin() as conn:
                for col_name, spec in missing.items():
                    sql_type = spec.get(dialect, spec['default'])
                    if dialect == 'sqlite':
                        stmt = f'ALTER TABLE {table_name} ADD COLUMN {col_name} {sql_type} NULL'
                    else:
                        stmt = f'ALTER TABLE {table_name} ADD {col_name} {sql_type} NULL'
                    log(f"  -> {stmt}")
                    conn.execute(text(stmt))
            log("Migration complete.")
            return True
    except Exception as e:
        log(f"WARNING — auto-migration could not run on {table_name}: {e}")
        log("If this persists, run manually: python migrate_add_defect_columns.py")
        return False


def ensure_defect_columns(app, verbose=True):
    """Idempotently add any missing columns to chimney_defects AND
    chimney_projects, and create any brand-new tables that this version of
    the app needs (e.g. help_tickets, chimney_project_access) but that
    don't exist on an existing database yet. Called automatically every
    time the Flask app starts (see app.py)."""
    from models import db

    def log(msg):
        if verbose:
            print(f"[migrate_add_defect_columns] {msg}")

    # db.create_all() only ever creates tables that don't already exist —
    # it never touches or drops existing ones — so this is safe to call
    # unconditionally on every startup.
    try:
        with app.app_context():
            db.create_all()
    except Exception as e:
        log(f"WARNING — could not auto-create any missing tables: {e}")

    ok1 = _ensure_columns(app, 'chimney_defects', NEW_DEFECT_COLUMNS, verbose)
    ok2 = _ensure_columns(app, 'chimney_projects', NEW_PROJECT_COLUMNS, verbose)
    ok3 = _ensure_columns(app, 'help_tickets', NEW_HELP_TICKET_COLUMNS, verbose)
    ok4 = _ensure_columns(app, 'activity_log', NEW_ACTIVITY_LOG_COLUMNS, verbose)
    ok5 = _ensure_columns(app, 'users', NEW_USER_COLUMNS, verbose)
    ok6 = _backfill_asset_category(app, verbose)
    ok7 = _rename_module(app, 'Chimney Inspection', '3D Inspection', verbose)
    ok8 = _ensure_columns(app, 'projects', NEW_GENERIC_PROJECT_COLUMNS, verbose)
    ok9 = _ensure_columns(app, 'divisions', NEW_DIVISION_COLUMNS, verbose)
    ok10 = _ensure_columns(app, 'lines', NEW_LINE_COLUMNS, verbose)
    ok11 = _ensure_columns(app, 'tower_defects', NEW_TOWER_DEFECT_COLUMNS, verbose)
    ok12 = _backfill_tower_defect_status(app, verbose)
    return (ok1 and ok2 and ok3 and ok4 and ok5 and ok6 and ok7 and ok8 and ok9
            and ok10 and ok11 and ok12)


def _backfill_tower_defect_status(app, verbose=True):
    """Existing tower_defects rows created before `status` existed get NULL
    there once the column is added — every one of those was, at the time,
    an open finding (there was no way to close one yet), so backfill to
    'Open' explicitly. Idempotent — a no-op once done."""
    from models import db

    def log(msg):
        if verbose:
            print(f"[migrate_add_defect_columns] {msg}")

    try:
        with app.app_context():
            engine = db.engine
            inspector = inspect(engine)
            if 'tower_defects' not in inspector.get_table_names():
                return True
            cols = {c['name'] for c in inspector.get_columns('tower_defects')}
            if 'status' not in cols:
                return True
            with engine.begin() as conn:
                result = conn.execute(text(
                    "UPDATE tower_defects SET status = 'Open' "
                    "WHERE status IS NULL OR status = ''"
                ))
                if result.rowcount:
                    log(f"Backfilled status='Open' on {result.rowcount} existing tower defect(s).")
            return True
    except Exception as e:
        log(f"WARNING — could not backfill tower_defects.status: {e}")
        return False


def _backfill_asset_category(app, verbose=True):
    """Existing chimney_projects rows created before asset_category existed
    get NULL there once the column is added (ALTER TABLE doesn't retroactively
    apply the ORM-side default) — every one of those rows WAS a chimney, so
    backfill them to 'chimney' explicitly. Idempotent — a no-op once done."""
    from models import db

    def log(msg):
        if verbose:
            print(f"[migrate_add_defect_columns] {msg}")

    try:
        with app.app_context():
            engine = db.engine
            inspector = inspect(engine)
            if 'chimney_projects' not in inspector.get_table_names():
                return True
            cols = {c['name'] for c in inspector.get_columns('chimney_projects')}
            if 'asset_category' not in cols:
                return True
            with engine.begin() as conn:
                result = conn.execute(text(
                    "UPDATE chimney_projects SET asset_category = 'chimney' "
                    "WHERE asset_category IS NULL OR asset_category = ''"
                ))
                if result.rowcount:
                    log(f"Backfilled asset_category='chimney' on {result.rowcount} existing project(s).")
            return True
    except Exception as e:
        log(f"WARNING — could not backfill asset_category: {e}")
        return False


def _rename_module(app, old_name, new_name, verbose=True):
    """Renames a Module row IN PLACE (old_name -> new_name) rather than
    creating a new one, so every user's existing module assignment (a
    foreign key to this row) stays intact. Safe/idempotent: no-op if
    old_name doesn't exist, or if new_name already exists."""
    from models import db

    def log(msg):
        if verbose:
            print(f"[migrate_add_defect_columns] {msg}")

    try:
        with app.app_context():
            engine = db.engine
            inspector = inspect(engine)
            if 'modules' not in inspector.get_table_names():
                return True
            with engine.begin() as conn:
                existing_new = conn.execute(
                    text("SELECT id FROM modules WHERE name = :n"), {'n': new_name}
                ).first()
                if existing_new:
                    return True  # already renamed
                result = conn.execute(
                    text("UPDATE modules SET name = :new WHERE name = :old"),
                    {'new': new_name, 'old': old_name}
                )
                if result.rowcount:
                    log(f"Renamed module '{old_name}' -> '{new_name}' (preserving existing user assignments).")
            return True
    except Exception as e:
        log(f"WARNING — could not rename module '{old_name}' -> '{new_name}': {e}")
        return False


def main():
    from app import create_app
    app = create_app()
    ok = ensure_defect_columns(app)
    if not ok:
        sys.exit(1)


if __name__ == '__main__':
    main()
