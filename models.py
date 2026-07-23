"""
models.py — SQLAlchemy ORM models for NOVA+
Tables:
    users               — one row per account
    user_roles          — many-to-many: user ↔ role  (Admin / Client User)
    user_modules        — many-to-many: user ↔ module (Transmission Line / …)
"""
import json
from datetime import datetime, timedelta
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# ── Association tables (pure join tables, no extra columns) ───────────────────

user_roles = db.Table(
    'user_roles',
    db.Column('user_id',  db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
    db.Column('role_id',  db.Integer, db.ForeignKey('roles.id',  ondelete='CASCADE'), primary_key=True),
)

user_modules = db.Table(
    'user_modules',
    db.Column('user_id',   db.Integer, db.ForeignKey('users.id',   ondelete='CASCADE'), primary_key=True),
    db.Column('module_id', db.Integer, db.ForeignKey('modules.id', ondelete='CASCADE'), primary_key=True),
)

# Project-wise access — an ADDITIONAL, optional restriction layered on top
# of module-level access. A user still needs their module assigned (via
# user_modules above) to see a module at all; these tables narrow that
# down further to specific projects within it, for the two different
# "project" concepts this app has (the generic Project model used by
# Transmission Line/Land Survey/TRANS, and ChimneyProject used by 3D
# Inspection). See User.allowed_project_ids_for()/is_admin-bypass logic
# for how "no rows here" intentionally means "full access to every
# project in that module" — the backward-compatible default so this
# doesn't retroactively lock out any existing user the moment it ships.
user_projects = db.Table(
    'user_projects',
    db.Column('user_id',    db.Integer, db.ForeignKey('users.id',    ondelete='CASCADE'), primary_key=True),
    db.Column('project_id', db.Integer, db.ForeignKey('projects.id', ondelete='CASCADE'), primary_key=True),
)

user_chimney_projects = db.Table(
    'user_chimney_projects',
    db.Column('user_id',            db.Integer, db.ForeignKey('users.id',            ondelete='CASCADE'), primary_key=True),
    db.Column('chimney_project_id', db.Integer, db.ForeignKey('chimney_projects.id', ondelete='CASCADE'), primary_key=True),
)


# ── Lookup tables ─────────────────────────────────────────────────────────────

class Role(db.Model):
    __tablename__ = 'roles'
    id   = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)

    def __repr__(self):
        return f'<Role {self.name}>'


class Module(db.Model):
    __tablename__ = 'modules'
    id    = db.Column(db.Integer, primary_key=True)
    name  = db.Column(db.String(100), unique=True, nullable=False)
    route = db.Column(db.String(100))   # Flask route name, e.g. 'projects'

    def __repr__(self):
        return f'<Module {self.name}>'


# ── Main user table ───────────────────────────────────────────────────────────

class User(db.Model):
    __tablename__ = 'users'

    id           = db.Column(db.Integer, primary_key=True)
    username     = db.Column(db.String(120), nullable=False)
    email        = db.Column(db.String(200), unique=True, nullable=False)
    password_hash= db.Column(db.String(256), nullable=False)
    contact      = db.Column(db.String(30),  default='')
    photo_path   = db.Column(db.String(255), default='')          # relative to /static
    status       = db.Column(db.String(20),  default='Pending')   # Active / Inactive / Pending
    last_login   = db.Column(db.String(40),  default='Never')     # display string, e.g. "14 Jul 2026, 08:30 AM"
    last_login_at = db.Column(db.DateTime, nullable=True)         # real timestamp, used to compute effective_status()
    created_at   = db.Column(db.DateTime,    default=datetime.utcnow)
    dashboard_notes = db.Column(db.Text, default='')  # personal scratch notes shown on the admin dashboard

    # How many days since last login before a user is considered inactive
    # again — drives both the Dashboard "Active Users" count and the
    # status shown in User Management, so the two always agree.
    ACTIVE_WINDOW_DAYS = 3

    def effective_status(self) -> str:
        """The status actually shown to admins: 'Pending' accounts stay
        Pending until approved; everyone else is 'Active' only if they've
        logged in within the last ACTIVE_WINDOW_DAYS, otherwise 'Inactive'
        — regardless of whatever the stored `status` column says, so this
        never drifts out of sync with real login activity."""
        if self.status == 'Pending':
            return 'Pending'
        if self.last_login_at and (datetime.utcnow() - self.last_login_at) <= timedelta(days=self.ACTIVE_WINDOW_DAYS):
            return 'Active'
        return 'Inactive'

    # Relationships
    roles   = db.relationship('Role',   secondary=user_roles,   backref='users', lazy='joined')
    modules = db.relationship('Module', secondary=user_modules, backref='users', lazy='joined')
    allowed_projects = db.relationship('Project', secondary=user_projects, backref='allowed_users')
    allowed_chimney_projects = db.relationship('ChimneyProject', secondary=user_chimney_projects, backref='allowed_users')

    # ── Convenience helpers ───────────────────────────────────────────────────

    def role_names(self) -> list[str]:
        return [r.name for r in self.roles]

    def module_names(self) -> list[str]:
        return [m.name for m in self.modules]

    def module_routes(self) -> dict:
        return {m.name: m.route for m in self.modules}

    def restricted_project_ids_for_module(self, module_name: str):
        """None means "no restriction — full access to every project in
        this module" (the backward-compatible default). A set (even an
        empty one, though that shouldn't normally happen from the UI)
        means "only these specific project IDs are visible"."""
        ids = {p.id for p in self.allowed_projects if p.module == module_name}
        return ids if ids else None

    def restricted_chimney_project_ids(self):
        """Same idea as restricted_project_ids_for_module(), but for
        ChimneyProject (3D Inspection) — that module doesn't have a
        `module` field to filter by since it's the only module using this
        table, so there's nothing to key off besides "any rows at all"."""
        ids = {p.id for p in self.allowed_chimney_projects}
        return ids if ids else None

    def to_dict(self, include_password: bool = False) -> dict:
        d = {
            'id':         self.id,
            'username':   self.username,
            'email':      self.email,
            'contact':    self.contact or '',
            'photo_url':  f'/static/{self.photo_path}' if self.photo_path else '',
            'roles':      self.role_names(),
            'modules':    self.module_names(),
            'status':     self.effective_status(),
            'last_login': self.last_login,
            'created_at': self.created_at.strftime('%d %b %Y') if self.created_at else '',
            'allowed_project_ids':          [p.id for p in self.allowed_projects],
            'allowed_chimney_project_ids':  [p.id for p in self.allowed_chimney_projects],
        }
        if include_password:
            d['password_hash'] = self.password_hash
        return d

    def __repr__(self):
        return f'<User {self.email}>'


# ── App-wide settings (key/value) ──────────────────────────────────────────────
# Currently used for the single shared "delete password" required to delete
# any project (Transmission Line / Land Survey / Chimney). Set/changed from
# the Settings page by an Admin.

class AppSetting(db.Model):
    __tablename__ = 'app_settings'
    key   = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.String(255), nullable=False)


# All timestamps are stored in UTC (datetime.utcnow()) — this deployment is
# India-based, so anywhere a raw timestamp is formatted for display, it's
# converted with this +5:30 offset first so it actually matches local time.
IST_OFFSET = timedelta(hours=5, minutes=30)


def _fmt_ist(dt, fmt='%d %b %Y, %I:%M %p', suffix=True):
    if not dt:
        return ''
    out = (dt + IST_OFFSET).strftime(fmt)
    return out + ' IST' if suffix else out


# ── Activity log (audit trail) ─────────────────────────────────────────────────
# One row per meaningful action — currently project/module deletions, shown on
# the Settings → Activity page along with active users and login details.

class ActivityLog(db.Model):
    __tablename__ = 'activity_log'

    id           = db.Column(db.Integer, primary_key=True)
    action       = db.Column(db.String(40),  nullable=False)   # e.g. 'delete'
    entity_type  = db.Column(db.String(40),  nullable=False)   # 'Project' / 'ChimneyProject'
    entity_name  = db.Column(db.String(150), default='')
    module       = db.Column(db.String(60),  default='')       # Transmission Line / Land Survey / Chimney Inspection
    performed_by = db.Column(db.String(150), default='')       # username at time of action
    role         = db.Column(db.String(20),  default='')       # Admin / Client User — role active at time of action
    duration_seconds = db.Column(db.Integer, nullable=True)    # for 'logout' rows: how long that session lasted
    details      = db.Column(db.String(255), default='')
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id':           self.id,
            'action':       self.action,
            'entity_type':  self.entity_type,
            'entity_name':  self.entity_name,
            'module':       self.module,
            'performed_by': self.performed_by,
            'role':         self.role or '',
            'duration':     _format_duration(self.duration_seconds) if self.duration_seconds is not None else '',
            'details':      self.details,
            'created_at':   _fmt_ist(self.created_at),
            'created_at_iso': self.created_at.isoformat() + 'Z' if self.created_at else '',
        }

    @staticmethod
    def log(action, entity_type, entity_name='', module='', performed_by='', details='', role='', duration_seconds=None):
        entry = ActivityLog(action=action, entity_type=entity_type, entity_name=entity_name,
                             module=module, performed_by=performed_by, details=details,
                             role=role, duration_seconds=duration_seconds)
        db.session.add(entry)
        return entry


def _format_duration(seconds):
    if seconds is None:
        return ''
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, _ = divmod(rem, 60)
    if h:
        return f'{h}h {m}m'
    if m:
        return f'{m}m'
    return '<1m'


# ── Dynamic Projects (replaces hardcoded MPPTCL / DVC / Kothegudam pages) ─────

class Project(db.Model):
    """
    A project belongs to a module (Transmission Line / Land Survey / Chimney
    Inspection / …). Created dynamically through the "+ Add Project" dialog
    on each module's listing page.
    """
    __tablename__ = 'projects'

    id          = db.Column(db.Integer, primary_key=True)
    module      = db.Column(db.String(60),  nullable=False)   # e.g. 'Transmission Line'
    name        = db.Column(db.String(150), nullable=False)
    contact_no  = db.Column(db.String(30),  default='')
    email       = db.Column(db.String(200), default='')       # project owner email
    country     = db.Column(db.String(100), default='')
    state       = db.Column(db.String(100), default='')
    logo_path   = db.Column(db.String(255), default='')       # relative to /static
    # Extra fields used by the TRANS module's richer "+ Add Project" form —
    # nullable/optional so they don't affect any other module's projects.
    client_name       = db.Column(db.String(150), default='')
    planned_divisions = db.Column(db.Integer, nullable=True)   # how many divisions the project is expected to have
    planned_towers    = db.Column(db.Integer, nullable=True)   # total towers expected across the project
    timeline          = db.Column(db.String(150), default='')  # free-form, e.g. "6 months" or a target date
    # Optional pointer to a legacy, hand-built template for the original demo
    # projects (MPPTCL / DVC / Kothegudam) so they keep working unchanged.
    legacy_route   = db.Column(db.String(80), default='')
    legacy_banner  = db.Column(db.String(255), default='')
    created_by  = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    divisions = db.relationship('Division', backref='project', cascade='all, delete-orphan',
                                 order_by='Division.created_at')

    def to_dict(self):
        return {
            'id':            self.id,
            'module':        self.module,
            'name':          self.name,
            'contact_no':    self.contact_no or '',
            'email':         self.email or '',
            'country':       self.country or '',
            'state':         self.state or '',
            'logo_url':      f'/static/{self.logo_path}' if self.logo_path else '',
            'client_name':       self.client_name or '',
            'planned_divisions': self.planned_divisions,
            'planned_towers':    self.planned_towers,
            'timeline':          self.timeline or '',
            'legacy_route':  self.legacy_route or '',
            'legacy_banner': self.legacy_banner or '',
            'division_count': len(self.divisions),
            'line_count':    sum(len(d.lines) for d in self.divisions),
            'created_at':    self.created_at.strftime('%d %b %Y') if self.created_at else '',
        }


class Division(db.Model):
    """A Division (GOMD/area) belonging to a Transmission Line project.
    Rendered as a tab in the left sidebar of the project map."""
    __tablename__ = 'divisions'

    id         = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False)
    name       = db.Column(db.String(150), nullable=False)
    latitude   = db.Column(db.Float, nullable=False)
    longitude  = db.Column(db.Float, nullable=False)
    # Extra fields for the richer "+ Add Division" form — nullable/optional,
    # doesn't affect divisions created before these existed.
    client_name    = db.Column(db.String(150), default='')
    state          = db.Column(db.String(100), default='')
    planned_towers = db.Column(db.Integer, nullable=True)   # towers expected to be covered in this division
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    lines = db.relationship('Line', backref='division', cascade='all, delete-orphan',
                             order_by='Line.created_at')

    def to_dict(self):
        return {
            'id':        self.id,
            'project_id': self.project_id,
            'name':      self.name,
            'latitude':  self.latitude,
            'longitude': self.longitude,
            'client_name':    self.client_name or '',
            'state':          self.state or '',
            'planned_towers': self.planned_towers,
            'line_count': len(self.lines),
        }


class Line(db.Model):
    """A transmission line belonging to a Division."""
    __tablename__ = 'lines'

    id           = db.Column(db.Integer, primary_key=True)
    division_id  = db.Column(db.Integer, db.ForeignKey('divisions.id', ondelete='CASCADE'), nullable=False)
    name         = db.Column(db.String(150), nullable=False)
    start_lat    = db.Column(db.Float, nullable=False)
    start_lng    = db.Column(db.Float, nullable=False)
    end_lat      = db.Column(db.Float, nullable=False)
    end_lng      = db.Column(db.Float, nullable=False)
    length_km    = db.Column(db.Float, default=0)
    tower_count  = db.Column(db.Integer, default=0)
    kml_path     = db.Column(db.String(255), default='')   # relative to /static
    # Used in the per-tower report's "General Information" section — set
    # once per line (a survey flight typically covers a whole line at
    # once), rather than re-entered per tower or per report.
    voltage_level    = db.Column(db.String(50), default='')
    survey_date      = db.Column(db.Date, nullable=True)
    pilot_name       = db.Column(db.String(150), default='')
    inspection_name  = db.Column(db.String(150), default='')
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id':          self.id,
            'division_id': self.division_id,
            'name':        self.name,
            'start':       {'lat': self.start_lat, 'lng': self.start_lng},
            'end':         {'lat': self.end_lat,   'lng': self.end_lng},
            'length_km':   self.length_km or 0,
            'tower_count': self.tower_count or 0,
            'kml_url':     f'/static/{self.kml_path}' if self.kml_path else '',
            'voltage_level':   self.voltage_level or '',
            'survey_date':     self.survey_date.strftime('%Y-%m-%d') if self.survey_date else '',
            'pilot_name':      self.pilot_name or '',
            'inspection_name': self.inspection_name or '',
        }


class TowerPhoto(db.Model):
    """A photo attached to one tower point on a Line's map.

    Tower points themselves come from parsing the Line's KML file
    client-side on every page load — they're not individual database rows.
    tower_label (the point's name/number as it appears in the KML, e.g.
    "T12") together with line_id is what stably identifies "this same
    tower" across page loads, so photos stay attached to the right point
    even though the point itself isn't a persisted record."""
    __tablename__ = 'tower_photos'

    id          = db.Column(db.Integer, primary_key=True)
    line_id     = db.Column(db.Integer, db.ForeignKey('lines.id', ondelete='CASCADE'), nullable=False)
    tower_label = db.Column(db.String(150), nullable=False)
    image_path  = db.Column(db.String(255), nullable=False)
    uploaded_by = db.Column(db.String(120), default='')
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id':          self.id,
            'line_id':     self.line_id,
            'tower_label': self.tower_label,
            'image_url':   f'/static/{self.image_path}' if self.image_path else '',
            'uploaded_by': self.uploaded_by or '',
            'created_at':  self.created_at.strftime('%d %b %Y %H:%M') if self.created_at else '',
            'defect_count': len(self.defects),
        }


class PilotAssignment(db.Model):
    """A Pilot assigned to fly/photograph a specific Line. seen_by_pilot
    drives the "new assignment" notification popup on the Pilot's
    dashboard — set False when Admin assigns (or reassigns) the line,
    flipped True once the Pilot has actually seen it there."""
    __tablename__ = 'pilot_assignments'
    __table_args__ = (db.UniqueConstraint('line_id', 'pilot_user_id', name='uq_pilot_line'),)

    id            = db.Column(db.Integer, primary_key=True)
    line_id       = db.Column(db.Integer, db.ForeignKey('lines.id', ondelete='CASCADE'), nullable=False)
    pilot_user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    assigned_by   = db.Column(db.String(120), default='')
    assigned_at   = db.Column(db.DateTime, default=datetime.utcnow)
    seen_by_pilot = db.Column(db.Boolean, default=False)

    line  = db.relationship('Line')
    pilot = db.relationship('User')

    def to_dict(self):
        return {
            'id': self.id, 'line_id': self.line_id, 'pilot_user_id': self.pilot_user_id,
            'pilot_name': self.pilot.username if self.pilot else '',
            'assigned_by': self.assigned_by or '',
            'assigned_at': self.assigned_at.strftime('%d %b %Y, %H:%M') if self.assigned_at else '',
            'seen_by_pilot': bool(self.seen_by_pilot),
        }


class TowerInspectionStatus(db.Model):
    """Per-tower "Inspection Done" flag — line_id + tower_label together
    identify the tower (same pattern as TowerPhoto, since towers aren't
    their own database rows). A tower with zero marked defects could
    either be a genuinely good tower or one nobody has finished reviewing
    yet — this flag is how Admin explicitly says "done, this one's
    reviewed" rather than the report generator or the client guessing
    from defect count alone. Both report generation and client-facing
    visibility of a tower's photos/defects are gated on this being True."""
    __tablename__ = 'tower_inspection_status'
    __table_args__ = (db.UniqueConstraint('line_id', 'tower_label', name='uq_tower_inspection'),)

    id             = db.Column(db.Integer, primary_key=True)
    line_id        = db.Column(db.Integer, db.ForeignKey('lines.id', ondelete='CASCADE'), nullable=False)
    tower_label    = db.Column(db.String(150), nullable=False)
    inspection_done = db.Column(db.Boolean, default=False)
    marked_by      = db.Column(db.String(120), default='')
    marked_at      = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            'line_id': self.line_id, 'tower_label': self.tower_label,
            'inspection_done': bool(self.inspection_done),
            'marked_by': self.marked_by or '',
            'marked_at': self.marked_at.strftime('%d %b %Y %H:%M') if self.marked_at else '',
        }


class TowerDefect(db.Model):
    """A defect marked directly on a tower photo (polygon/rectangle/circle
    drawn over the 2D image, plus a short observation form) — this is the
    TRANS module's equivalent of the chimney module's 3D defect markings,
    just on a flat photo instead of a 3D model.

    shape_coords are stored as PERCENTAGES of the image's width/height
    (0-100), not raw pixels — that keeps the marking correctly aligned
    regardless of what size the image happens to be displayed at, since
    percentage-of-image-bounds is resolution-independent while raw pixel
    coordinates would only be correct at the exact display size they were
    drawn at."""
    __tablename__ = 'tower_defects'

    id             = db.Column(db.Integer, primary_key=True)
    tower_photo_id = db.Column(db.Integer, db.ForeignKey('tower_photos.id', ondelete='CASCADE'), nullable=False)
    shape_type     = db.Column(db.String(20), nullable=False)   # 'polygon' | 'rect' | 'circle'
    shape_coords   = db.Column(db.Text, nullable=False)         # JSON list of {x, y} in % of image bounds
    component_name = db.Column(db.String(150), default='')
    location       = db.Column(db.String(20), default='')       # 'Top' | 'Middle' | 'Bottom'
    defect_type    = db.Column(db.String(100), default='')      # e.g. 'Corrosion', 'Broken Insulator'
    observation    = db.Column(db.String(255), default='')
    severity       = db.Column(db.String(20), default='Minor')  # matches the chimney module's severity vocabulary
    status         = db.Column(db.String(20), default='Open')   # 'Open' | 'Closed'
    comments       = db.Column(db.Text, default='')
    created_by     = db.Column(db.String(120), default='')
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)

    photo = db.relationship('TowerPhoto', backref=db.backref('defects', cascade='all, delete-orphan',
                                                               order_by='TowerDefect.created_at'))

    def to_dict(self):
        coords = []
        if self.shape_coords:
            try:
                coords = json.loads(self.shape_coords)
            except (TypeError, ValueError):
                coords = []
        return {
            'id':              self.id,
            'tower_photo_id':  self.tower_photo_id,
            'shape_type':      self.shape_type,
            'shape_coords':    coords,
            'component_name':  self.component_name or '',
            'location':        self.location or '',
            'defect_type':     self.defect_type or '',
            'observation':     self.observation or '',
            'severity':        self.severity or 'Minor',
            'status':          self.status or 'Open',
            'comments':        self.comments or '',
            'created_by':      self.created_by or '',
            'created_at':      self.created_at.strftime('%d %b %Y %H:%M') if self.created_at else '',
        }


class TowerReport(db.Model):
    """A generated per-tower PDF report (RGB Visual Inspection Report).
    Generation is Admin-only; once generated, the file is stored on disk
    (like tower photos) with this row pointing at it, so Client sessions
    can download the already-generated report without being able to
    trigger generation themselves. Re-generating for the same tower
    replaces the existing row/file rather than accumulating duplicates."""
    __tablename__ = 'tower_reports'

    id           = db.Column(db.Integer, primary_key=True)
    line_id      = db.Column(db.Integer, db.ForeignKey('lines.id', ondelete='CASCADE'), nullable=False)
    tower_label  = db.Column(db.String(150), nullable=False)
    report_path  = db.Column(db.String(255), nullable=False)   # relative to /static
    generated_by = db.Column(db.String(120), default='')
    generated_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id':           self.id,
            'line_id':      self.line_id,
            'tower_label':  self.tower_label,
            'report_url':   f'/static/{self.report_path}' if self.report_path else '',
            'generated_by': self.generated_by or '',
            'generated_at': self.generated_at.strftime('%d %b %Y %H:%M') if self.generated_at else '',
        }


# ── Chimney Inspection module (self-contained — does not touch Project/Division/Line,
#    which stay dedicated to Transmission Line / Land Survey) ─────────────────────────

class ChimneyProject(db.Model):
    """
    One chimney/stack asset under inspection. Created via the "+ Add Chimney"
    form (Asset name, Inspection type, Structure type, Lat/Long). Once saved,
    opening it shows the 3D viewer + inspection tools page.
    """
    __tablename__ = 'chimney_projects'

    id              = db.Column(db.Integer, primary_key=True)
    # Which kind of 3D Inspection asset this is — chosen via the "Project
    # Type" dropdown when creating it. Determines which page opening the
    # project shows (chimney -> full 3D viewer, water_tank -> its own
    # cover page, currently a placeholder). Defaults to 'chimney' so every
    # project created before this field existed keeps behaving exactly as
    # it did.
    asset_category  = db.Column(db.String(30), default='chimney', nullable=False)
    asset_name      = db.Column(db.String(150), nullable=False)   # e.g. "Unit-2 Flue Stack"
    inspection_type = db.Column(db.String(60),  default='')       # Visual / Thermal / Structural / Combined
    structure_type  = db.Column(db.String(60),  default='')       # RCC / Brick / Steel / FRP
    latitude        = db.Column(db.Float, nullable=False)
    longitude       = db.Column(db.Float, nullable=False)
    tileset_path    = db.Column(db.String(255), default='')       # relative to /static, points at tileset.json
    # A JPEG snapshot of the fully-loaded model, captured automatically
    # the first time an Admin session finishes loading it. Shown as an
    # instant placeholder image on top of the 3D container on every later
    # visit, faded out once the live model is actually ready — so the
    # page looks "already there" immediately instead of a blank loading
    # state, even though the interactive scene still has to build behind
    # it. Doesn't change how the tileset itself loads/streams.
    cover_snapshot_path = db.Column(db.String(255), default='')
    status          = db.Column(db.String(30),  default='Active')
    created_by      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at      = db.Column(db.DateTime, default=datetime.utcnow)
    # The actual geographic centre of the loaded 3D tileset (lon/lat of its
    # bounding-sphere centre, reported by the client once the model has
    # loaded). The manually-entered latitude/longitude above is just the
    # asset's approximate registered location and can be meters to
    # kilometers off from where the tileset itself is geo-referenced —
    # using it as the compass reference point is what made "Direction"
    # come out wrong/inconsistent for defects. model_center_* is the
    # correct reference point and is preferred whenever it's available.
    model_center_lat = db.Column(db.Float, nullable=True)
    model_center_lng = db.Column(db.Float, nullable=True)
    # Overall height of the structure in meters, calculated automatically
    # client-side once the 3D model finishes loading (top of the model's
    # true geometric extent minus the detected ground level — see
    # reportStructureHeight() in chimney_viewer.js) — shown on the Project
    # Timeline card in the project overview. Null until a model has been
    # uploaded and opened at least once.
    structure_height_m = db.Column(db.Float, nullable=True)
    # Free-text description of what this inspection covers, shown on the
    # report cover page (e.g. "Full 360° visual survey of the flue stack
    # shell, including base, shaft and top rim, via drone-based 3D scan").
    inspection_scope = db.Column(db.Text, default='')
    # Free-text, comma-separated list of pilot names who flew this
    # inspection — shown on the project's Detailed View page. Kept as
    # simple text rather than a separate table since crews are usually
    # small and named informally (e.g. "Ravi Kumar, Ajay S.").
    pilots           = db.Column(db.Text, default='')
    # Optional target/expected completion date, set when the project is
    # created (or edited later) — shown on the Detailed View timeline card.
    target_completion_date = db.Column(db.Date, nullable=True)

    defects = db.relationship('ChimneyDefect', backref='chimney_project',
                               cascade='all, delete-orphan', order_by='ChimneyDefect.created_at')

    def direction_reference(self):
        """The lat/lon to measure defect compass directions from.
        Preference order:
        1. The average position of this project's own defects — needs no
           3D geometry sampling at all, so this is the most reliable
           option: with defects spread around the structure (the normal
           case for a real inspection), their centroid sits close to the
           structure's own centre. Needs at least 3 defects to be a
           reasonably meaningful average.
        2. model_center_lat/lng — the model's real geometric centre,
           reported by the client from ray-casting against the loaded 3D
           tiles. Used when there aren't enough defects yet for a
           reliable average. Can be skewed if that ray-casting has
           trouble (e.g. on a tall/thin structure, or a scan that
           includes a lot of surrounding terrain beyond the structure
           itself) — which is exactly what caused every defect to
           previously compute the same, wrong compass direction — so
           it's intentionally not the first choice once real defect data
           exists to correct for it.
        3. The manually-entered project location — a last-resort
           approximation only, since it may be an approximate site pin
           rather than the structure's precise position.
        """
        located = [d for d in self.defects if d.pos_lat is not None and d.pos_lon is not None]
        if len(located) >= 3:
            avg_lat = sum(d.pos_lat for d in located) / len(located)
            avg_lon = sum(d.pos_lon for d in located) / len(located)
            return avg_lat, avg_lon
        if self.model_center_lat is not None and self.model_center_lng is not None:
            return self.model_center_lat, self.model_center_lng
        return self.latitude, self.longitude

    def pilots_list(self):
        return [p.strip() for p in (self.pilots or '').split(',') if p.strip()]

    def completion_summary(self):
        """Returns a dict describing progress toward closing out every
        finding on this project — used by the Detailed View page."""
        total = len(self.defects)
        closed = [d for d in self.defects if (d.status or 'Open') == 'Closed']
        closed_count = len(closed)
        open_count = total - closed_count
        is_complete = total > 0 and closed_count == total
        days = None
        if is_complete:
            last_closed = max((d.closed_at for d in closed if d.closed_at), default=None)
            if last_closed and self.created_at:
                days = (last_closed.date() - self.created_at.date()).days
        elif self.created_at:
            days = (datetime.utcnow().date() - self.created_at.date()).days
        return {
            'total': total,
            'open': open_count,
            'closed': closed_count,
            'open_pct': round(open_count / total * 100) if total else 0,
            'closed_pct': round(closed_count / total * 100) if total else 0,
            'is_complete': is_complete,
            'days': days,
            'target_date': self.target_completion_date,
            'days_to_target': (self.target_completion_date - datetime.utcnow().date()).days if self.target_completion_date and not is_complete else None,
        }

    def to_dict(self):
        return {
            'id':              self.id,
            'asset_category':  self.asset_category or 'chimney',
            'asset_name':      self.asset_name,
            'inspection_type': self.inspection_type or '',
            'structure_type':  self.structure_type or '',
            'latitude':        self.latitude,
            'longitude':       self.longitude,
            'model_center_lat': self.model_center_lat,
            'model_center_lng': self.model_center_lng,
            'structure_height_m': self.structure_height_m,
            'inspection_scope': self.inspection_scope or '',
            'tileset_url':     f'/static/{self.tileset_path}' if self.tileset_path else '',
            'has_model':       bool(self.tileset_path),
            'status':          self.status or 'Active',
            'defect_count':    len(self.defects),
            'created_at':      self.created_at.strftime('%d %b %Y') if self.created_at else '',
        }


def compass_direction(from_lat, from_lon, to_lat, to_lon):
    """Return an 8-point compass direction (N/NE/E/SE/S/SW/W/NW) describing
    where (to_lat, to_lon) sits relative to (from_lat, from_lon)."""
    import math
    if from_lat is None or from_lon is None or to_lat is None or to_lon is None:
        return ''
    phi1 = math.radians(from_lat)
    phi2 = math.radians(to_lat)
    d_lambda = math.radians(to_lon - from_lon)

    x = math.sin(d_lambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(d_lambda)
    theta = math.atan2(x, y)
    bearing = (math.degrees(theta) + 360) % 360

    dirs = ['North', 'North-East', 'East', 'South-East',
            'South', 'South-West', 'West', 'North-West']
    idx = round(bearing / 45) % 8
    return dirs[idx]


class ChimneyDefect(db.Model):
    """An inspection finding pinned to a point on the chimney's 3D model."""
    __tablename__ = 'chimney_defects'

    id                 = db.Column(db.Integer, primary_key=True)
    chimney_project_id = db.Column(db.Integer, db.ForeignKey('chimney_projects.id', ondelete='CASCADE'), nullable=False)
    title              = db.Column(db.String(150), nullable=False)
    severity           = db.Column(db.String(20), default='Minor')   # Minor / Moderate / Critical
    status             = db.Column(db.String(20), default='Open')    # Open / Closed — tracks whether the finding has been resolved
    rectified_image_path = db.Column(db.String(255), default='')     # relative to /static — proof-of-fix photo, required to close a finding
    closed_at          = db.Column(db.DateTime, nullable=True)
    closed_by          = db.Column(db.String(120), default='')
    notes              = db.Column(db.Text, default='')
    defect_type        = db.Column(db.String(60), default='')       # Crack / Spalling / Corrosion / … (or a custom value typed in via "Other")
    area               = db.Column(db.String(60), default='')       # e.g. "1-5 m2" or a calculated value
    height_label       = db.Column(db.String(40), default='')       # display string for distance from ground, e.g. "12.4 m"
    location           = db.Column(db.String(150), default='')      # free-text location description, entered manually (e.g. "North face, near flange")
    image_path         = db.Column(db.String(255), default='')      # relative to /static — saved defect snapshot
    # Pin position on the 3D model, stored as world lon/lat/height so it can be
    # re-placed with Cesium.Cartesian3.fromDegrees(lon, lat, height).
    pos_lon            = db.Column(db.Float, nullable=False)
    pos_lat            = db.Column(db.Float, nullable=False)
    pos_height         = db.Column(db.Float, default=0)
    # If this finding was drawn with the Line/Rectangle/Polygon/Circle tool
    # (rather than just a single click), the shape's own geometry is kept
    # here so it can be redrawn on every reload — separate from pos_lon/
    # pos_lat/pos_height above, which stay the centroid used for the pin/label.
    shape_type         = db.Column(db.String(20), nullable=True)    # 'line' | 'rect' | 'polygon' | 'circle' | None
    shape_coords       = db.Column(db.Text, nullable=True)          # JSON list of {lon, lat, height} — raw anchor/click points
    # The FULLY-COMPUTED, surface-hugging outline (dense list of
    # {lon, lat, height} points) — the actual result of fitting
    # shape_coords onto the model's surface via ray-casting, done ONCE at
    # creation time and saved here. Every earlier version of this instead
    # re-derived this fit fresh via ray-casting every time the project
    # page loaded, against WHATEVER 3D tile detail happened to be loaded
    # at that particular moment — which is why the same shape could look
    # subtly different (a gap, or sunk into a surface bump) between
    # sessions, even though nothing about the shape itself had changed:
    # the tile detail available at the wide overview zoom right after a
    # fresh page load is rarely the same as the close-up detail that was
    # loaded when it was originally drawn. Storing the actual result
    # instead of re-deriving it sidesteps that entirely — reloading the
    # project just replays this fixed data. Nullable for backward
    # compatibility with defects saved before this existed, which fall
    # back to the old live re-derivation.
    rendered_positions = db.Column(db.Text, nullable=True)
    # The EXACT camera pose (world ECEF position + view direction + up,
    # each as raw x/y/z) used to capture this defect's close-up image.
    # Stored so flyToDefect() can fly back to the identical view later
    # instead of recomputing it live — recomputing depends on whatever 3D
    # tile detail happens to be loaded/cached at that moment, which drifts
    # as the model gets orbited around over time, and was what made
    # clicking the same defect minutes later land on a different,
    # off-angle view than the one actually captured.
    cam_pos_x = db.Column(db.Float, nullable=True)
    cam_pos_y = db.Column(db.Float, nullable=True)
    cam_pos_z = db.Column(db.Float, nullable=True)
    cam_dir_x = db.Column(db.Float, nullable=True)
    cam_dir_y = db.Column(db.Float, nullable=True)
    cam_dir_z = db.Column(db.Float, nullable=True)
    cam_up_x  = db.Column(db.Float, nullable=True)
    cam_up_y  = db.Column(db.Float, nullable=True)
    cam_up_z  = db.Column(db.Float, nullable=True)
    created_at         = db.Column(db.DateTime, default=datetime.utcnow)

    def direction(self):
        """Compass direction of this defect relative to the chimney's real
        geometric centre (falls back to the manually-entered project
        lat/lon if the model hasn't reported its centre yet)."""
        proj = self.chimney_project
        if not proj:
            return ''
        ref_lat, ref_lon = proj.direction_reference()
        return compass_direction(ref_lat, ref_lon, self.pos_lat, self.pos_lon)

    def to_dict(self):
        coords = None
        if self.shape_coords:
            try:
                coords = json.loads(self.shape_coords)
            except (TypeError, ValueError):
                coords = None
        rendered = None
        if self.rendered_positions:
            try:
                rendered = json.loads(self.rendered_positions)
            except (TypeError, ValueError):
                rendered = None
        cam_pos = None
        cam_dir = None
        cam_up = None
        if self.cam_pos_x is not None and self.cam_pos_y is not None and self.cam_pos_z is not None:
            cam_pos = {'x': self.cam_pos_x, 'y': self.cam_pos_y, 'z': self.cam_pos_z}
        if self.cam_dir_x is not None and self.cam_dir_y is not None and self.cam_dir_z is not None:
            cam_dir = {'x': self.cam_dir_x, 'y': self.cam_dir_y, 'z': self.cam_dir_z}
        if self.cam_up_x is not None and self.cam_up_y is not None and self.cam_up_z is not None:
            cam_up = {'x': self.cam_up_x, 'y': self.cam_up_y, 'z': self.cam_up_z}
        return {
            'id':           self.id,
            'title':        self.title,
            'severity':     self.severity or 'Minor',
            'status':       self.status or 'Open',
            'rectified_image_url': f'/static/{self.rectified_image_path}' if self.rectified_image_path else '',
            'closed_at':    _fmt_ist(self.closed_at, '%d %b %Y %H:%M', suffix=False),
            'closed_by':    self.closed_by or '',
            'notes':        self.notes or '',
            'defect_type':  self.defect_type or '',
            'area':         self.area or '',
            'height':       self.height_label or '',
            'location':     self.location or '',
            'direction':    self.direction(),
            'image_url':    f'/static/{self.image_path}' if self.image_path else '',
            'position':     {'lon': self.pos_lon, 'lat': self.pos_lat, 'height': self.pos_height or 0},
            'shape_type':   self.shape_type,
            'shape_coords': coords,
            'rendered_positions': rendered,
            'cam_pos':      cam_pos,
            'cam_dir':      cam_dir,
            'cam_up':       cam_up,
            'created_at':   _fmt_ist(self.created_at, '%d %b %Y %H:%M', suffix=False),
        }


class ChimneyProjectAccess(db.Model):
    """Tracks who has opened a given chimney project (viewer or Detailed
    View), for the "who is accessing this project" panel. One row per
    user per project — upserted on every visit rather than logging every
    single hit, so the table stays small."""
    __tablename__ = 'chimney_project_access'

    id                  = db.Column(db.Integer, primary_key=True)
    chimney_project_id  = db.Column(db.Integer, db.ForeignKey('chimney_projects.id', ondelete='CASCADE'), nullable=False)
    user_name           = db.Column(db.String(120), nullable=False)
    role                = db.Column(db.String(20), default='')
    visit_count         = db.Column(db.Integer, default=1)
    first_accessed_at   = db.Column(db.DateTime, default=datetime.utcnow)
    last_accessed_at    = db.Column(db.DateTime, default=datetime.utcnow)

    @staticmethod
    def record(chimney_project_id, user_name, role):
        if not user_name:
            return
        row = ChimneyProjectAccess.query.filter_by(
            chimney_project_id=chimney_project_id, user_name=user_name
        ).first()
        if row:
            row.visit_count = (row.visit_count or 0) + 1
            row.last_accessed_at = datetime.utcnow()
            row.role = role or row.role
        else:
            row = ChimneyProjectAccess(
                chimney_project_id=chimney_project_id, user_name=user_name, role=role or ''
            )
            db.session.add(row)
        db.session.commit()

    def to_dict(self):
        return {
            'user_name': self.user_name,
            'role': self.role or '',
            'visit_count': self.visit_count or 1,
            'last_accessed_at': _fmt_ist(self.last_accessed_at, '%d %b %Y %H:%M', suffix=False),
        }


class Announcement(db.Model):
    """Admin-posted announcements shown as a bell popup on the home page.
    Replaces the earlier Help-ticket system — this is one-way (Admin ->
    everyone) rather than users raising individual problems."""
    __tablename__ = 'announcements'

    id          = db.Column(db.Integer, primary_key=True)
    title       = db.Column(db.String(150), nullable=False)
    message     = db.Column(db.Text, default='')
    image_path  = db.Column(db.String(255), default='')   # relative to /static
    created_by  = db.Column(db.String(120), default='')
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'message': self.message or '',
            'image_url': f'/static/{self.image_path}' if self.image_path else '',
            'created_by': self.created_by or '',
            'created_at': _fmt_ist(self.created_at, suffix=False),
        }


class HelpTicket(db.Model):
    """A help/support request raised from Settings → Help, by an Admin,
    Client, or Pilot describing a problem. Feeds the top notification
    bell — Admin works through each ticket via the 'Checking' /
    'Problem Resolved' actions."""
    __tablename__ = 'help_tickets'

    STATUSES = ('Open', 'Checking', 'Resolved')

    id                 = db.Column(db.Integer, primary_key=True)
    subject            = db.Column(db.String(150), nullable=False)
    description        = db.Column(db.Text, default='')
    reporter_type      = db.Column(db.String(20), default='Client')  # Client / Pilot / Admin — self-selected at submission
    submitted_by       = db.Column(db.String(120), default='')
    status             = db.Column(db.String(20), default='Open')    # Open (new) / Checking / Resolved
    created_at         = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at         = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    resolved_by        = db.Column(db.String(120), default='')
    seen_by_reporter   = db.Column(db.Boolean, default=True)   # flips to False whenever Admin updates status, so the raiser gets notified

    def to_dict(self):
        return {
            'id': self.id,
            'subject': self.subject,
            'description': self.description or '',
            'reporter_type': self.reporter_type or 'Client',
            'submitted_by': self.submitted_by or '',
            'status': self.status or 'Open',
            'created_at': _fmt_ist(self.created_at, '%d %b %Y %H:%M', suffix=False),
            'updated_at': _fmt_ist(self.updated_at, '%d %b %Y %H:%M', suffix=False),
            'resolved_by': self.resolved_by or '',
            'seen_by_reporter': self.seen_by_reporter if self.seen_by_reporter is not None else True,
        }
