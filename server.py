#!/usr/bin/env python3
"""Tiny todo-md backend. Stores markdown files per magic-link token. Includes SMTP magic link auth."""

import os
import uuid
import json
import re
import sqlite3
import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote
from pathlib import Path

DATA_DIR = os.environ.get("TODO_DATA_DIR", "/opt/todo-data")
PORT = int(os.environ.get("TODO_PORT", "8070"))
STATIC_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_URL = os.environ.get("BASE_URL", f"http://localhost:{PORT}")
API_KEY = os.environ.get("BINNACLE_API_KEY", "")

# SMTP config
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "Todo <noreply@example.com>")

# Simple token validation — UUID4 format only
TOKEN_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')

# --- SQLite for users and magic links ---
DB_PATH = os.path.join(DATA_DIR, "auth.db")

def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS users (email TEXT PRIMARY KEY, token TEXT UNIQUE NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS magic_links (code TEXT PRIMARY KEY, email TEXT NOT NULL, expires_at REAL NOT NULL, used INTEGER DEFAULT 0)")
    conn.execute("CREATE TABLE IF NOT EXISTS categories (token TEXT NOT NULL, name TEXT NOT NULL, icon TEXT DEFAULT 'folder', color TEXT DEFAULT '#346665', sort_order INTEGER DEFAULT 0, PRIMARY KEY(token, name))")
    conn.commit()
    conn.close()

def get_db():
    return sqlite3.connect(DB_PATH)

def send_magic_email(to_email, magic_url):
    """Send magic link email via SMTP."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Your Todo Link"
    msg["From"] = SMTP_FROM
    msg["To"] = to_email

    text = f"Click to sign in:\n\n{magic_url}\n\nThis link expires in 15 minutes."
    html = f"""<div style="font-family:sans-serif;max-width:400px;margin:0 auto;padding:20px">
        <h2 style="font-size:20px">Todo</h2>
        <p>Click the button to sign in to your todo lists:</p>
        <a href="{magic_url}" style="display:inline-block;padding:12px 24px;background:#1a1a1a;color:#fff;text-decoration:none;border-radius:6px;margin:16px 0">Open My Todos</a>
        <p style="font-size:12px;color:#999">This link expires in 15 minutes.</p>
    </div>"""

    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)

def safe_token(t):
    return bool(t and TOKEN_RE.match(t))

def safe_name(n):
    """Sanitize filename — alphanums, hyphens, underscores, slashes for folders."""
    if not n:
        return None
    if '..' in n:
        return None
    parts = [re.sub(r'[^a-zA-Z0-9_\- ]', '', p).strip() for p in n.split('/')]
    parts = [p for p in parts if p]
    if not parts:
        return None
    return '/'.join(parts)


def get_token_for_email(email):
    """Look up or create a user token by email."""
    email = email.lower().strip()
    db = get_db()
    row = db.execute("SELECT token FROM users WHERE email = ?", (email,)).fetchone()
    if row:
        token = row[0]
    else:
        token = str(uuid.uuid4())
        db.execute("INSERT INTO users (email, token) VALUES (?, ?)", (email, token))
        os.makedirs(os.path.join(DATA_DIR, token), exist_ok=True)
        db.commit()
    db.close()
    return token


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # quiet

    def _check_api_key(self):
        """Validate Bearer token against BINNACLE_API_KEY. Returns True if valid."""
        if not API_KEY:
            self._json(503, {"error": "API key not configured on server"})
            return False
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            key = auth[7:].strip()
        else:
            key = auth.strip()
        if key != API_KEY:
            self._json(401, {"error": "Invalid API key"})
            return False
        return True

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, POST, DELETE, PATCH, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _text(self, code, text):
        body = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/markdown; charset=utf-8")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_PATCH(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        # PATCH /api/v1/{email}/{list_name}/{line_number} — toggle or update a task line
        m = re.match(r'^/api/v1/([^/]+@[^/]+)/(.+)/(\d+)$', path)
        if m:
            if not self._check_api_key():
                return
            email, name, line_num = m.group(1), m.group(2), int(m.group(3))
            token = get_token_for_email(unquote(email))
            name = safe_name(unquote(name))
            if not name:
                return self._json(400, {"error": "invalid name"})
            fp = os.path.join(DATA_DIR, token, name.replace('/', os.sep) + ".md")
            if not os.path.isfile(fp):
                return self._json(404, {"error": "list not found"})

            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            action = body.get("action", "toggle")  # toggle, update, check, uncheck

            with open(fp, "r") as fh:
                lines = fh.readlines()

            if line_num < 0 or line_num >= len(lines):
                return self._json(400, {"error": f"line {line_num} out of range (0-{len(lines)-1})"})

            line = lines[line_num]
            if action == "toggle":
                if "- [ ] " in line:
                    line = line.replace("- [ ] ", "- [x] ", 1)
                elif "- [x] " in line:
                    line = line.replace("- [x] ", "- [ ] ", 1)
            elif action == "check":
                line = line.replace("- [ ] ", "- [x] ", 1)
            elif action == "uncheck":
                line = line.replace("- [x] ", "- [ ] ", 1)
            elif action == "update":
                new_text = body.get("text", "")
                # Preserve the checkbox prefix
                if line.strip().startswith("- [ ] "):
                    line = f"- [ ] {new_text}\n"
                elif line.strip().startswith("- [x] "):
                    line = f"- [x] {new_text}\n"
                else:
                    line = new_text + "\n"

            lines[line_num] = line
            with open(fp, "w") as fh:
                fh.writelines(lines)
            return self._json(200, {"ok": True, "line": line_num, "content": line.rstrip()})

        return self._json(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        # POST /api/v1/{email}/{list_name}/tasks — append a task
        m = re.match(r'^/api/v1/([^/]+@[^/]+)/(.+)/tasks$', path)
        if m:
            if not self._check_api_key():
                return
            email, name = m.group(1), m.group(2)
            token = get_token_for_email(unquote(email))
            name = safe_name(unquote(name))
            if not name:
                return self._json(400, {"error": "invalid name"})

            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            task_text = body.get("task", "").strip()
            checked = body.get("checked", False)
            if not task_text:
                return self._json(400, {"error": "task text required"})

            token_dir = os.path.join(DATA_DIR, token)
            os.makedirs(token_dir, exist_ok=True)
            fp = os.path.join(token_dir, name.replace('/', os.sep) + ".md")
            os.makedirs(os.path.dirname(fp), exist_ok=True)

            prefix = "- [x] " if checked else "- [ ] "
            line = prefix + task_text + "\n"

            # Append (create file if needed)
            existing = ""
            if os.path.isfile(fp):
                with open(fp, "r") as fh:
                    existing = fh.read()
            with open(fp, "w") as fh:
                if existing and not existing.endswith("\n"):
                    existing += "\n"
                fh.write(existing + line)

            return self._json(201, {"ok": True, "task": task_text, "checked": checked})

        # POST /api/v1/{email}/folders — create a folder
        m = re.match(r'^/api/v1/([^/]+@[^/]+)/folders$', path)
        if m:
            if not self._check_api_key():
                return
            email = unquote(m.group(1))
            token = get_token_for_email(email)
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            folder_name = safe_name(body.get("name", ""))
            if not folder_name:
                return self._json(400, {"error": "folder name required"})
            folder_path = os.path.join(DATA_DIR, token, folder_name.replace('/', os.sep))
            os.makedirs(folder_path, exist_ok=True)
            return self._json(201, {"ok": True, "folder": folder_name})

        # POST /api/v1/{email} — create a new list
        m = re.match(r'^/api/v1/([^/]+@[^/]+)$', path)
        if m:
            if not self._check_api_key():
                return
            email = m.group(1)
            token = get_token_for_email(unquote(email))

            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            name = safe_name(body.get("name", ""))
            content = body.get("content", "")
            if not name:
                return self._json(400, {"error": "name required"})

            token_dir = os.path.join(DATA_DIR, token)
            os.makedirs(token_dir, exist_ok=True)
            fp = os.path.join(token_dir, name.replace('/', os.sep) + ".md")
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            with open(fp, "w") as fh:
                fh.write(content)
            return self._json(201, {"ok": True, "name": name})

        # POST /api/categories/{token} — save categories for a user
        m = re.match(r'^/api/categories/([^/]+)$', path)
        if m:
            token = m.group(1)
            if not safe_token(token):
                return self._json(400, {"error": "invalid token"})
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            categories = body.get("categories", [])
            db = get_db()
            db.execute("DELETE FROM categories WHERE token = ?", (token,))
            for i, cat in enumerate(categories):
                cat_name = (cat.get("name") or "").strip()
                if not cat_name:
                    continue
                db.execute("INSERT OR REPLACE INTO categories (token, name, icon, color, sort_order) VALUES (?, ?, ?, ?, ?)",
                           (token, cat_name, cat.get("icon", "folder"), cat.get("color", "#346665"), i))
            db.commit()
            db.close()
            return self._json(200, {"ok": True})

        if path == "/api/auth/send-link":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            email = (body.get("email") or "").lower().strip()

            if not email or "@" not in email:
                return self._json(400, {"error": "Valid email required"})

            db = get_db()
            # Get or create user
            row = db.execute("SELECT token FROM users WHERE email = ?", (email,)).fetchone()
            if row:
                user_token = row[0]
            else:
                user_token = str(uuid.uuid4())
                db.execute("INSERT INTO users (email, token) VALUES (?, ?)", (email, user_token))
                # Create the file directory for this user
                os.makedirs(os.path.join(DATA_DIR, user_token), exist_ok=True)

            # Create magic link code
            code = str(uuid.uuid4())
            expires_at = time.time() + 15 * 60  # 15 min
            db.execute("INSERT INTO magic_links (code, email, expires_at) VALUES (?, ?, ?)", (code, email, expires_at))
            db.commit()
            db.close()

            magic_url = f"{BASE_URL}/api/auth/verify/{code}"

            if SMTP_HOST:
                try:
                    send_magic_email(email, magic_url)
                    return self._json(200, {"ok": True, "message": "Magic link sent! Check your email."})
                except Exception as e:
                    print(f"SMTP error: {e}")
                    return self._json(500, {"error": "Failed to send email. Try again."})
            else:
                # No SMTP — return link directly (dev mode)
                print(f"Magic link for {email}: {magic_url}")
                return self._json(200, {"ok": True, "message": "Magic link sent!", "dev_link": magic_url})

        return self._json(404, {"error": "not found"})

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        # --- External API v1 (API key auth, email-based) ---

        # GET /api/v1/{email} — list all files for a user
        m = re.match(r'^/api/v1/([^/]+@[^/]+)$', path)
        if m:
            if not self._check_api_key():
                return
            email = unquote(m.group(1))
            token = get_token_for_email(email)
            token_dir = os.path.join(DATA_DIR, token)
            if not os.path.isdir(token_dir):
                return self._json(200, {"email": email, "files": []})
            files = []
            for root, dirs, filenames in os.walk(token_dir):
                for f in filenames:
                    if f.endswith(".md"):
                        fp = os.path.join(root, f)
                        rel = os.path.relpath(fp, token_dir)
                        name = rel.replace(os.sep, '/')[:-3]
                        files.append({
                            "name": name,
                            "modified": os.path.getmtime(fp),
                            "size": os.path.getsize(fp)
                        })
            files.sort(key=lambda x: x["modified"], reverse=True)
            return self._json(200, {"email": email, "files": files})

        # GET /api/v1/{email}/{list_name} — get a specific file
        m = re.match(r'^/api/v1/([^/]+@[^/]+)/(.+)$', path)
        if m:
            if not self._check_api_key():
                return
            email, name = unquote(m.group(1)), m.group(2)
            token = get_token_for_email(email)
            name = safe_name(unquote(name))
            if not name:
                return self._json(400, {"error": "invalid name"})
            fp = os.path.join(DATA_DIR, token, name.replace('/', os.sep) + ".md")
            if not os.path.isfile(fp):
                return self._json(404, {"error": "not found"})
            with open(fp, "r") as fh:
                content = fh.read()
            # Also return structured task data
            tasks = []
            for i, line in enumerate(content.splitlines()):
                stripped = line.strip()
                if stripped.startswith("- [ ] "):
                    tasks.append({"line": i, "text": stripped[6:], "checked": False})
                elif stripped.startswith("- [x] "):
                    tasks.append({"line": i, "text": stripped[6:], "checked": True})
            return self._json(200, {"name": name, "content": content, "tasks": tasks})

        # Serve static files
        if path == "" or path == "/":
            return self._serve_file("index.html", "text/html")

        # Magic link verify
        m = re.match(r'^/api/auth/verify/([0-9a-f-]+)$', path)
        if m:
            code = m.group(1)
            db = get_db()
            row = db.execute("SELECT email, expires_at, used FROM magic_links WHERE code = ?", (code,)).fetchone()
            if not row or row[2]:
                db.close()
                self.send_response(400)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"Invalid or expired link.")
                return
            email, expires_at, _ = row
            if time.time() > expires_at:
                db.execute("UPDATE magic_links SET used = 1 WHERE code = ?", (code,))
                db.commit()
                db.close()
                self.send_response(400)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"Link expired. Request a new one.")
                return
            # Mark used, get user token
            db.execute("UPDATE magic_links SET used = 1 WHERE code = ?", (code,))
            user_row = db.execute("SELECT token FROM users WHERE email = ?", (email,)).fetchone()
            db.commit()
            db.close()
            if not user_row:
                self.send_response(400)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"User not found.")
                return
            # Redirect to app with token
            redirect_url = f"{BASE_URL}/?t={user_row[0]}"
            self.send_response(302)
            self.send_header("Location", redirect_url)
            self.end_headers()
            return

        # API: generate new token
        if path == "/api/new":
            token = str(uuid.uuid4())
            token_dir = os.path.join(DATA_DIR, token)
            os.makedirs(token_dir, exist_ok=True)
            return self._json(200, {"token": token})

        # API: get categories for a token
        m = re.match(r'^/api/categories/([^/]+)$', path)
        if m:
            token = m.group(1)
            if not safe_token(token):
                return self._json(400, {"error": "invalid token"})
            db = get_db()
            rows = db.execute("SELECT name, icon, color FROM categories WHERE token = ? ORDER BY sort_order", (token,)).fetchall()
            db.close()
            cats = [{"name": r[0], "icon": r[1], "color": r[2]} for r in rows]
            return self._json(200, {"categories": cats})

        # API: list all files for a token (recursive)
        m = re.match(r'^/api/lists/([^/]+)$', path)
        if m:
            token = m.group(1)
            if not safe_token(token):
                return self._json(400, {"error": "invalid token"})
            token_dir = os.path.join(DATA_DIR, token)
            if not os.path.isdir(token_dir):
                return self._json(200, {"files": []})
            files = []
            for root, dirs, filenames in os.walk(token_dir):
                for f in filenames:
                    if f.endswith(".md"):
                        fp = os.path.join(root, f)
                        rel = os.path.relpath(fp, token_dir)
                        # Convert path separators to / and strip .md
                        name = rel.replace(os.sep, '/')[:-3]
                        files.append({
                            "name": name,
                            "modified": os.path.getmtime(fp),
                            "size": os.path.getsize(fp)
                        })
            files.sort(key=lambda x: x["modified"], reverse=True)
            return self._json(200, {"files": files})

        # API: get a specific file (supports folder paths like token/Category/listname)
        m = re.match(r'^/api/lists/([^/]+)/(.+)$', path)
        if m:
            token, name = m.group(1), m.group(2)
            if not safe_token(token):
                return self._json(400, {"error": "invalid token"})
            name = safe_name(unquote(name))
            if not name:
                return self._json(400, {"error": "invalid name"})
            fp = os.path.join(DATA_DIR, token, name.replace('/', os.sep) + ".md")
            if not os.path.isfile(fp):
                return self._json(404, {"error": "not found"})
            with open(fp, "r") as fh:
                return self._text(200, fh.read())

        return self._json(404, {"error": "not found"})

    def do_PUT(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        # PUT /api/v1/{email}/{list_name} — overwrite a list's content
        m = re.match(r'^/api/v1/([^/]+@[^/]+)/([^/]+)$', path)
        if m:
            if not self._check_api_key():
                return
            email, name = unquote(m.group(1)), m.group(2)
            token = get_token_for_email(email)
            name = safe_name(unquote(name))
            if not name:
                return self._json(400, {"error": "invalid name"})
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8") if length else ""
            token_dir = os.path.join(DATA_DIR, token)
            os.makedirs(token_dir, exist_ok=True)
            fp = os.path.join(token_dir, name + ".md")
            with open(fp, "w") as fh:
                fh.write(body)
            return self._json(200, {"ok": True, "name": name})

        m = re.match(r'^/api/lists/([^/]+)/(.+)$', path)
        if not m:
            return self._json(404, {"error": "not found"})

        token, name = m.group(1), m.group(2)
        if not safe_token(token):
            return self._json(400, {"error": "invalid token"})
        name = safe_name(unquote(name))
        if not name:
            return self._json(400, {"error": "invalid name"})

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else ""

        token_dir = os.path.join(DATA_DIR, token)
        os.makedirs(token_dir, exist_ok=True)
        fp = os.path.join(token_dir, name.replace('/', os.sep) + ".md")
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "w") as fh:
            fh.write(body)

        return self._json(200, {"ok": True, "name": name})

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        # DELETE /api/v1/{email}/{list_name} — delete a list
        m = re.match(r'^/api/v1/([^/]+@[^/]+)/([^/]+)$', path)
        if m:
            if not self._check_api_key():
                return
            email, name = unquote(m.group(1)), m.group(2)
            token = get_token_for_email(email)
            name = safe_name(unquote(name))
            if not name:
                return self._json(400, {"error": "invalid name"})
            fp = os.path.join(DATA_DIR, token, name + ".md")
            if os.path.isfile(fp):
                os.remove(fp)
                return self._json(200, {"ok": True})
            return self._json(404, {"error": "not found"})

        # DELETE /api/categories/{token}/{catName} — delete a category
        m = re.match(r'^/api/categories/([^/]+)/(.+)$', path)
        if m:
            token, cat_name = m.group(1), unquote(m.group(2))
            if not safe_token(token):
                return self._json(400, {"error": "invalid token"})
            db = get_db()
            db.execute("DELETE FROM categories WHERE token = ? AND name = ?", (token, cat_name))
            db.commit()
            db.close()
            return self._json(200, {"ok": True})

        m = re.match(r'^/api/lists/([^/]+)/(.+)$', path)
        if not m:
            return self._json(404, {"error": "not found"})

        token, name = m.group(1), m.group(2)
        if not safe_token(token):
            return self._json(400, {"error": "invalid token"})
        name = safe_name(unquote(name))
        if not name:
            return self._json(400, {"error": "invalid name"})

        fp = os.path.join(DATA_DIR, token, name.replace('/', os.sep) + ".md")
        if os.path.isfile(fp):
            os.remove(fp)
            return self._json(200, {"ok": True})
        return self._json(404, {"error": "not found"})

    def _serve_file(self, filename, content_type):
        fp = os.path.join(STATIC_DIR, filename)
        if not os.path.isfile(fp):
            return self._json(404, {"error": "not found"})
        with open(fp, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self._cors()
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    init_db()
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"todo-md server on :{PORT}, data in {DATA_DIR}")
    if SMTP_HOST:
        print(f"SMTP configured: {SMTP_HOST}:{SMTP_PORT} as {SMTP_USER}")
    else:
        print("No SMTP configured — magic links will be logged to console")
    if API_KEY:
        print(f"External API enabled (key configured)")
    else:
        print("External API disabled (set BINNACLE_API_KEY to enable)")
    server.serve_forever()
