# NOVA+ Flask Application

Power Transmission Intelligence Platform — rebuilt with Flask.

## Project Structure

```
nova_flask/
├── app.py                  # Flask application & routes
├── requirements.txt
├── static/
│   └── data/
│       └── towers.json     # DVC tower & GOMD data (served via /api/towers)
└── templates/
    ├── base.html           # Shared layout (navbar, CSS, Leaflet CDN)
    ├── home.html           # Landing page with animated SVG cover
    ├── login.html          # Login form (POST → session)
    ├── register.html       # Register form (POST → session)
    ├── admin.html          # Admin dashboard (modules + user management)
    ├── projects.html       # Project selection (DVC, MPPTCL)
    ├── dvc.html            # DVC interactive Leaflet map
    └── mpptcl.html         # MPPTCL map (placeholder)
```

## How to Run

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start the development server
python app.py

# 3. Open in browser
http://localhost:5000
```

## Key Improvements over Single-File HTML

| Feature | Old (vibe-coded HTML) | New (Flask) |
|---|---|---|
| Auth | JS array in browser (insecure) | Server-side session with `flask.session` |
| Routing | JS `show()` function | Proper URL routes (`/login`, `/admin`, `/dvc`) |
| Data | Inline JS `const STATIC_TOWERS` | JSON file served via `/api/towers` API |
| Templates | One giant HTML file | Jinja2 templates with `base.html` inheritance |
| Security | Passwords visible in source | Passwords never sent to browser |
| Extensibility | Hard to add features | Add new routes & templates easily |

## Adding a Database (Next Step)

Replace the in-memory `users = []` list in `app.py` with SQLAlchemy:

```bash
pip install flask-sqlalchemy
```

```python
from flask_sqlalchemy import SQLAlchemy
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///nova.db'
db = SQLAlchemy(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    email = db.Column(db.String(120), unique=True)
    password_hash = db.Column(db.String(200))
```

## Environment Variables (Production)

```bash
export SECRET_KEY="your-strong-random-secret"
export FLASK_ENV=production
```
