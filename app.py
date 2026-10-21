import io
import os
import re
import sys
import time
import zipfile
import sqlite3
import secrets
import shutil
import subprocess
from pathlib import Path
from functools import wraps
from threading import Lock

from dotenv import load_dotenv
from flask import Flask, request, redirect, url_for, session, flash, render_template_string, abort

load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-only-change-me")
app.config["MAX_CONTENT_LENGTH"] = max(1, int(os.getenv("MAX_UPLOAD_MB", "25"))) * 1024 * 1024 + 1024
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
DATA_DIR = Path(os.getenv("DATA_DIR", "data")).resolve()
BOTS_DIR = Path(os.getenv("BOTS_DIR", "bots")).resolve()
MAX_UPLOAD_MB = max(1, int(os.getenv("MAX_UPLOAD_MB", "25")))
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
DB_PATH = DATA_DIR / "panel.sqlite3"
ENTRY_CANDIDATES = ("main.py", "bot.py", "app.py", "start.py", "run.py")
processes = {}
process_lock = Lock()
DATA_DIR.mkdir(parents=True, exist_ok=True)
BOTS_DIR.mkdir(parents=True, exist_ok=True)

CSS = """
:root{color-scheme:dark;--bg:#0b1020;--panel:#141b2e;--line:#28334b;--text:#edf2ff;--muted:#9aa8c7;--accent:#fb5265}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px system-ui,sans-serif}
a{color:#ff9aa6;text-decoration:none}header{display:flex;align-items:center;justify-content:space-between;padding:18px max(18px,calc((100% - 1050px)/2));border-bottom:1px solid var(--line);background:#0e1527}
main{max-width:1050px;margin:28px auto;padding:0 16px}.brand{font-weight:800;font-size:20px}.muted,small{color:var(--muted)}.card{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:20px;margin:16px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(245px,1fr));gap:14px}.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.between{justify-content:space-between}
input,button{font:inherit;border-radius:10px;padding:11px 13px}input{width:100%;background:#0c1324;color:var(--text);border:1px solid var(--line);margin:6px 0 14px}
button,.btn{display:inline-block;border:0;background:var(--accent);color:#fff;padding:10px 14px;font-weight:700;cursor:pointer;border-radius:10px}.secondary{background:#293550}.danger{background:#8d3040}button:disabled{opacity:.45;cursor:not-allowed}label{color:var(--muted);font-size:13px}
h1{font-size:27px;margin:0 0 8px}h2{font-size:18px;margin:0 0 12px}.pill{font-size:12px;padding:4px 8px;border-radius:999px;background:#243b35;color:#8ff0c3}.stopped{background:#3c3040;color:#f6a8b6}
.notice{padding:11px 14px;background:#25344b;border-radius:10px;margin:12px 0}.logs{white-space:pre-wrap;overflow-wrap:anywhere;background:#080d18;border:1px solid var(--line);padding:12px;border-radius:10px;max-height:360px;overflow:auto;font:12px ui-monospace,monospace}
form.inline{display:inline}@media(max-width:600px){header{padding:15px}main{margin-top:18px}.card{padding:15px}}
"""
BASE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{{ title }} · MUNIX Bot Hosting</title><style>""" + CSS + """</style></head><body><header><div class="brand">MUNIX <span style="color:#fb5265">BOT HOSTING</span></div>{% if session.get('logged_in') %}<a href="{{ url_for('logout') }}">Log out</a>{% endif %}</header><main>{% for message in get_flashed_messages() %}<div class="notice">{{ message }}</div>{% endfor %}{{ body|safe }}</main></body></html>"""
LOGIN = """
<div class="card" style="max-width:430px;margin:8vh auto"><h1>Owner login</h1><p class="muted">Sign in to manage bot projects.</p><form method="post"><label>Username</label><input name="username" autocomplete="username" required><label>Password</label><input type="password" name="password" autocomplete="current-password" required><button style="width:100%">Login</button></form></div>
"""
DASH = """
<div class="row between"><div><h1>Bot dashboard</h1><p class="muted">Manage Python bot projects on this host.</p></div><span class="pill">{{ bots|length }} project(s)</span></div>
<div class="card"><h2>Upload a bot ZIP</h2><p class="muted">ZIP must contain an entry file at its root or one folder deep. Upload trusted code only.</p><form method="post" action="{{ url_for('upload') }}" enctype="multipart/form-data"><input type="file" name="archive" accept=".zip" required><button>Upload project</button></form><small>Maximum upload: {{ max_mb }} MB. requirements.txt is installed when starting a bot.</small></div>
<div class="grid">{% for bot in bots %}<section class="card"><div class="row between"><h2>{{ bot.name }}</h2><span class="pill {{ 'stopped' if bot.status != 'Running' else '' }}">{{ bot.status }}</span></div><p class="muted">Entry: {{ bot.entry }}</p><div class="row">
<form class="inline" method="post" action="{{ url_for('action', bot_id=bot.id, operation='start') }}"><button {% if bot.status == 'Running' %}disabled{% endif %}>Start</button></form>
<form class="inline" method="post" action="{{ url_for('action', bot_id=bot.id, operation='restart') }}"><button class="secondary">Restart</button></form>
<form class="inline" method="post" action="{{ url_for('action', bot_id=bot.id, operation='stop') }}"><button class="secondary">Stop</button></form>
<a class="btn secondary" href="{{ url_for('logs', bot_id=bot.id) }}">Logs</a></div><form method="post" action="{{ url_for('delete_bot', bot_id=bot.id) }}" onsubmit="return confirm('Delete this bot and its files?')"><button class="danger" style="margin-top:12px">Delete</button></form></section>{% else %}<div class="card"><h2>No bots yet</h2><p class="muted">Upload a ZIP to add your first bot.</p></div>{% endfor %}</div>
<div class="card"><h2>Runtime note</h2><p class="muted">Processes may stop when the host restarts or sleeps. Use a persistent host for reliable uptime.</p></div>
"""

def connect_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

with connect_db() as con:
    con.execute("""CREATE TABLE IF NOT EXISTS bots(id TEXT PRIMARY KEY,name TEXT NOT NULL,folder TEXT NOT NULL,entry TEXT NOT NULL,created REAL NOT NULL)""")

def page(title, body, **context):
    inner = render_template_string(body, **context)
    return render_template_string(BASE, title=title, body=inner)

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapper

def get_bot_rows():
    with connect_db() as con:
        rows = con.execute("SELECT * FROM bots ORDER BY created DESC").fetchall()
    result = []
    with process_lock:
        for row in rows:
            proc = processes.get(row["id"])
            status = "Running" if proc and proc.poll() is None else "Stopped"
            result.append({**dict(row), "status": status})
    return result

def get_bot(bot_id):
    with connect_db() as con:
        row = con.execute("SELECT * FROM bots WHERE id=?", (bot_id,)).fetchone()
    if row is None:
        abort(404)
    return dict(row)

def stop_process(bot_id):
    with process_lock:
        proc = processes.get(bot_id)
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        processes.pop(bot_id, None)

def find_entry(folder):
    for candidate in ENTRY_CANDIDATES:
        if (folder / candidate).is_file():
            return candidate
    dirs = [p for p in folder.iterdir() if p.is_dir() and p.name != "__pycache__"]
    if len(dirs) == 1:
        for candidate in ENTRY_CANDIDATES:
            if (dirs[0] / candidate).is_file():
                return str(Path(dirs[0].name) / candidate)
    return None

@app.get("/")
@login_required
def index():
    return page("Dashboard", DASH, bots=get_bot_rows(), max_mb=MAX_UPLOAD_MB)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if not ADMIN_PASSWORD:
            flash("Set ADMIN_PASSWORD in the environment before logging in.")
            return redirect(url_for("login"))
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if secrets.compare_digest(username, ADMIN_USERNAME) and secrets.compare_digest(password, ADMIN_PASSWORD):
            session.clear()
            session["logged_in"] = True
            return redirect(request.args.get("next") or url_for("index"))
        time.sleep(0.5)
        flash("Invalid username or password.")
    return page("Login", LOGIN)

@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.post("/upload")
@login_required
def upload():
    f = request.files.get("archive")
    if not f or not f.filename or not f.filename.lower().endswith(".zip"):
        flash("Choose a ZIP archive.")
        return redirect(url_for("index"))
    raw = f.read(MAX_UPLOAD_MB * 1024 * 1024 + 1)
    if len(raw) > MAX_UPLOAD_MB * 1024 * 1024:
        flash("ZIP exceeds the upload size limit.")
        return redirect(url_for("index"))
    bot_id = secrets.token_hex(6)
    name = re.sub(r"[^a-zA-Z0-9_-]+", "-", Path(f.filename).stem).strip("-")[:40] or "bot"
    folder = (BOTS_DIR / (name + "-" + bot_id)).resolve()
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            if len(members) > 500:
                raise ValueError("Archive contains too many files.")
            if sum(max(0, item.file_size) for item in members) > 100 * 1024 * 1024:
                raise ValueError("Extracted archive exceeds 100 MB.")
            for item in members:
                target = (folder / item.filename).resolve()
                if target != folder and folder not in target.parents:
                    raise ValueError("Unsafe path in ZIP.")
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(item) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
        entry = find_entry(folder)
        if not entry:
            raise ValueError("No main.py, bot.py, app.py, start.py, or run.py found.")
        (folder / "panel.log").write_text("Project uploaded.\n", encoding="utf-8")
        with connect_db() as con:
            con.execute("INSERT INTO bots(id,name,folder,entry,created) VALUES(?,?,?,?,?)", (bot_id, name, str(folder), entry, time.time()))
        flash("Project uploaded. Review its code, then start it.")
    except Exception as exc:
        shutil.rmtree(folder, ignore_errors=True)
        flash("Upload failed: " + str(exc)[:180])
    return redirect(url_for("index"))

@app.post("/bot/<bot_id>/<operation>")
@login_required
def action(bot_id, operation):
    bot = get_bot(bot_id)
    if operation == "stop":
        stop_process(bot_id)
        flash("Stop requested.")
    elif operation in ("start", "restart"):
        stop_process(bot_id)
        folder = Path(bot["folder"]).resolve()
        entry = (folder / bot["entry"]).resolve()
        if folder not in entry.parents or not entry.is_file():
            flash("Entry file is missing or invalid.")
            return redirect(url_for("index"))
        log_path = folder / "panel.log"
        try:
            with log_path.open("a", encoding="utf-8") as log:
                log.write("\n--- start " + time.strftime("%Y-%m-%d %H:%M:%S") + " ---\n")
                requirements = folder / "requirements.txt"
                if requirements.is_file():
                    result = subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(requirements)],
                        cwd=str(folder), stdout=log, stderr=subprocess.STDOUT, timeout=180)
                    if result.returncode != 0:
                        flash("Dependency installation failed. Check logs.")
                        return redirect(url_for("logs", bot_id=bot_id))
            log_handle = log_path.open("a", encoding="utf-8")
            proc = subprocess.Popen([sys.executable, "-u", str(entry)], cwd=str(folder),
                stdin=subprocess.DEVNULL, stdout=log_handle, stderr=subprocess.STDOUT,
                start_new_session=True, env={**os.environ, "PYTHONUNBUFFERED": "1"})
            proc._munix_log_handle = log_handle
            with process_lock:
                processes[bot_id] = proc
            flash("Bot process started.")
        except Exception as exc:
            flash("Could not start bot: " + str(exc)[:160])
    else:
        abort(404)
    return redirect(url_for("index"))

@app.get("/bot/<bot_id>/logs")
@login_required
def logs(bot_id):
    bot = get_bot(bot_id)
    path = Path(bot["folder"]) / "panel.log"
    try:
        content = path.read_text(encoding="utf-8", errors="replace")[-20000:]
    except OSError:
        content = "No logs available yet."
    body = """<div class="row between"><div><h1>{{ bot.name }} logs</h1><p class="muted">Latest output, up to 20 KB.</p></div><a class="btn secondary" href="{{ url_for('index') }}">Back</a></div><div class="card"><pre class="logs">{{ content }}</pre><form method="post" action="{{ url_for('clear_logs', bot_id=bot.id) }}"><button class="secondary">Clear logs</button></form></div>"""
    return page("Logs", body, bot=bot, content=content)

@app.post("/bot/<bot_id>/clear-logs")
@login_required
def clear_logs(bot_id):
    bot = get_bot(bot_id)
    (Path(bot["folder"]) / "panel.log").write_text("", encoding="utf-8")
    flash("Logs cleared.")
    return redirect(url_for("logs", bot_id=bot_id))

@app.post("/bot/<bot_id>/delete")
@login_required
def delete_bot(bot_id):
    bot = get_bot(bot_id)
    stop_process(bot_id)
    shutil.rmtree(Path(bot["folder"]), ignore_errors=True)
    with connect_db() as con:
        con.execute("DELETE FROM bots WHERE id=?", (bot_id,))
    flash("Project deleted.")
    return redirect(url_for("index"))

@app.errorhandler(413)
def too_large(_error):
    return "Upload too large. Reduce the ZIP size and try again.", 413

@app.after_request
def headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    return response

if __name__ == "__main__":
    if not ADMIN_PASSWORD:
        print("WARNING: Set ADMIN_PASSWORD and SECRET_KEY before exposing this panel.")
    app.run(host=HOST, port=PORT, debug=False)
