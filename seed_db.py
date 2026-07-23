"""
seed_db.py — Creates all tables and seeds the database with demo users.

Run once after setting up the project:
    python seed_db.py

Safe to re-run: existing users are skipped (matched by email).
"""
from app import create_app
from models import db, Role, Module
from users import _get_or_create_role, _get_or_create_module, ROLES, MODULES, MODULE_ROUTES
from werkzeug.security import generate_password_hash
import os

app = create_app()

SEED_USERS = [
    {
        'username': 'Sunil Kumar',
        'email':    'sunil@drogodrones.com',
        'password': 'admin123',
        'contact':  '+91 98765 43210',
        'roles':    ['Admin', 'Client User'],
        'modules':  ['Transmission Line', 'Land Survey', '3D Inspection'],
        'status':   'Active',
    },
    {
        'username': 'Ramesh Patel',
        'email':    'ramesh@drogodrones.com',
        'password': 'client123',
        'contact':  '+91 91234 56780',
        'roles':    ['Client User'],
        'modules':  ['Transmission Line'],
        'status':   'Active',
    },
    {
        'username': 'Anjali Sharma',
        'email':    'anjali@drogodrones.com',
        'password': 'client123',
        'contact':  '+91 99887 76655',
        'roles':    ['Client User'],
        'modules':  ['Land Survey'],
        'status':   'Pending',
    },
    {
        'username': 'Vikram Singh',
        'email':    'vikram@drogodrones.com',
        'password': 'client123',
        'contact':  '+91 90909 12121',
        'roles':    ['Client User'],
        'modules':  ['3D Inspection', 'Land Survey'],
        'status':   'Active',
    },
    {
        'username': 'Meena Rao',
        'email':    'meena@drogodrones.com',
        'password': 'admin123',
        'contact':  '+91 98001 22334',
        'roles':    ['Admin'],
        'modules':  ['Transmission Line', 'Land Survey', '3D Inspection'],
        'status':   'Inactive',
    },
]


def seed():
    from models import User

    os.makedirs('instance', exist_ok=True)

    with app.app_context():
        # ── Create all tables ─────────────────────────────────────────────────
        db.create_all()
        print("✓ Tables created (or already exist)")

        # ── Seed lookup tables ────────────────────────────────────────────────
        for r in ROLES:
            _get_or_create_role(r)
        for m in MODULES:
            mod = Module.query.filter_by(name=m).first()
            if not mod:
                db.session.add(Module(name=m, route=MODULE_ROUTES.get(m, '')))
            elif mod.route != MODULE_ROUTES.get(m, ''):
                # Keeps existing rows in sync if MODULE_ROUTES changes later
                # (e.g. Chimney Inspection used to point at the 'mpptcl' placeholder).
                mod.route = MODULE_ROUTES.get(m, '')
        db.session.commit()
        print("✓ Roles and modules seeded")

        # ── Seed users ────────────────────────────────────────────────────────
        created = 0
        skipped = 0
        for data in SEED_USERS:
            existing = User.query.filter_by(email=data['email']).first()
            if existing:
                skipped += 1
                continue

            user = User(
                username      = data['username'],
                email         = data['email'],
                password_hash = generate_password_hash(data['password']),
                contact       = data['contact'],
                status        = data['status'],
            )
            for role_name in data['roles']:
                user.roles.append(_get_or_create_role(role_name))
            for mod_name in data['modules']:
                user.modules.append(_get_or_create_module(mod_name))

            db.session.add(user)
            created += 1

        db.session.commit()
        print(f"✓ Users: {created} created, {skipped} already existed")

        # ── Seed demo Transmission Line projects ────────────────────────────────
        from models import Project, Division, Line

        if not Project.query.filter_by(name='Damodar Valley Corporation').first():
            db.session.add(Project(
                module='Transmission Line',
                name='Damodar Valley Corporation',
                contact_no='+91 90000 00001',
                email='owner@dvc.example.com',
                country='India', state='West Bengal / Jharkhand',
                logo_path='images/logos/dvc-logo.png',
                legacy_route='dvc',
                legacy_banner='https://images.pexels.com/photos/32599646/pexels-photo-32599646.jpeg?auto=compress&cs=tinysrgb&w=800',
            ))

        mpptcl = Project.query.filter_by(name='Madhya Pradesh Power Transmission').first()
        if not mpptcl:
            mpptcl = Project(
                module='Transmission Line',
                name='Madhya Pradesh Power Transmission',
                contact_no='+91 90000 00002',
                email='owner@mpptcl.example.com',
                country='India', state='Madhya Pradesh',
                logo_path='images/logos/mpptcl-logo.png',
            )
            db.session.add(mpptcl)
            db.session.flush()
            division = Division(project_id=mpptcl.id, name='Bhopal Division', latitude=23.2599, longitude=77.4126)
            db.session.add(division)
            db.session.flush()
            db.session.add(Line(
                division_id=division.id, name='Bhopal–Indore 400kV',
                start_lat=23.2599, start_lng=77.4126, end_lat=22.7196, end_lng=75.8577,
                length_km=190.5, tower_count=420,
            ))

        if not Project.query.filter_by(name='Kothegudam').first():
            db.session.add(Project(
                module='Land Survey',
                name='Kothegudam',
                contact_no='+91 90000 00003',
                email='owner@kothegudam.example.com',
                country='India', state='Telangana',
                legacy_route='land-survey/kothegudam',
                legacy_banner='/static/images/landsurvey_Cover.png',
            ))

        db.session.commit()
        print("✓ Demo projects seeded (DVC legacy, MPPTCL dynamic, Kothegudam legacy)")
        print()
        print("═══ Demo credentials ═══════════════════════════════")
        print("  Admin:       sunil@drogodrones.com  / admin123")
        print("  Admin:       meena@drogodrones.com  / admin123")
        print("  Client User: ramesh@drogodrones.com / client123")
        print("  Client User: anjali@drogodrones.com / client123")
        print("  Client User: vikram@drogodrones.com / client123")
        print("════════════════════════════════════════════════════")


if __name__ == '__main__':
    seed()
