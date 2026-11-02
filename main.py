#!/usr/bin/env python3
"""A minimal SQLite-backed CMS with an admin token."""
import argparse
import datetime as dt
import html
import http.server
import os
import sqlite3
import tempfile
import threading
import urllib.parse
import urllib.request

SCHEMA = """CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    updated TEXT NOT NULL
)"""

LAYOUT = """<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:52rem;margin:2rem auto;padding:0 1rem;line-height:1.6}}
nav a{{margin-right:1rem}} .meta{{color:#777;font-size:.85rem}} form{{display:grid;gap:.6rem;max-width:34rem}}
input,textarea{{font:inherit;padding:.4rem}} textarea{{min-height:10rem}}</style></head>
<body><nav><a href="/">Home</a><a href="/admin?token={token}">Admin</a></nav><hr>{content}</body></html>"""

def connect(path):
    connection = sqlite3.connect(path)
    connection.execute(SCHEMA)
    connection.commit()
    return connection

def render_list(pages):
    items = "".join(f'<li><a href="/page/{html.escape(slug)}">{html.escape(title)}</a> '
                    f'<span class="meta">updated {updated}</span></li>' for slug, title, updated in pages)
    return f"<h1>Pages</h1><ul>{items or '<li>No pages yet.</li>'}</ul>"

def render_page(title, body, updated):
    paragraphs = "".join(f"<p>{html.escape(part)}</p>" for part in body.split("\n\n") if part.strip())
    return f"<h1>{html.escape(title)}</h1><div class='meta'>updated {updated}</div>{paragraphs}"

def render_admin(pages, token):
    rows = "".join(
        f'<tr><td>{html.escape(slug)}</td><td>{html.escape(title)}</td><td>'
        f'<form method="post" action="/admin/delete"><input type="hidden" name="token" value="{token}">'
        f'<input type="hidden" name="slug" value="{html.escape(slug)}"><button>delete</button></form></td></tr>'
        for slug, title, _ in pages)
    return f"""<h1>Admin</h1><table>{rows}</table>
<h2>Create or update</h2>
<form method="post" action="/admin/save">
<input type="hidden" name="token" value="{token}">
<label>Slug <input name="slug" required pattern="[a-z0-9-]+"></label>
<label>Title <input name="title" required></label>
<label>Body <textarea name="body" required></textarea></label>
<button>Save</button></form>"""

def make_handler(db_path: str, token: str):
    class Handler(http.server.BaseHTTPRequestHandler):
        def _send(self, body: str, status=200):
            payload = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers(); self.wfile.write(payload)

        def _check_token(self, fields):
            return fields.get("token") == [token]

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(parsed.query)
            connection = connect(db_path)
            try:
                if parsed.path == "/":
                    pages = connection.execute("SELECT slug,title,updated FROM pages ORDER BY updated DESC").fetchall()
                    self._send(LAYOUT.format(title="CMS", token=token, content=render_list(pages)))
                elif parsed.path.startswith("/page/"):
                    slug = parsed.path.split("/page/", 1)[1]
                    row = connection.execute("SELECT title,body,updated FROM pages WHERE slug=?", (slug,)).fetchone()
                    if row:
                        self._send(LAYOUT.format(title=row[0], token=token, content=render_page(*row)))
                    else:
                        self._send(LAYOUT.format(title="Not found", token=token, content="<h1>404</h1>"), 404)
                elif parsed.path == "/admin":
                    pages = connection.execute("SELECT slug,title,updated FROM pages ORDER BY slug").fetchall()
                    self._send(LAYOUT.format(title="Admin", token=token, content=render_admin(pages, token)))
                else:
                    self._send(LAYOUT.format(title="Not found", token=token, content="<h1>404</h1>"), 404)
            finally:
                connection.close()

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            fields = urllib.parse.parse_qs(self.rfile.read(length).decode())
            if not self._check_token(fields):
                self._send(LAYOUT.format(title="Forbidden", token=token, content="<h1>Forbidden</h1>"), 403); return
            connection = connect(db_path)
            try:
                if self.path == "/admin/save":
                    slug = fields.get("slug", [""])[0].strip()
                    title = fields.get("title", [""])[0].strip()
                    body = fields.get("body", [""])[0]
                    if not slug or not title:
                        self._send(LAYOUT.format(title="Bad request", token=token, content="<h1>slug and title required</h1>"), 400); return
                    connection.execute(
                        "INSERT INTO pages (slug,title,body,updated) VALUES (?,?,?,?) "
                        "ON CONFLICT(slug) DO UPDATE SET title=excluded.title, body=excluded.body, updated=excluded.updated",
                        (slug, title, body, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")))
                    connection.commit()
                    self.send_response(303); self.send_header("Location", f"/page/{slug}"); self.end_headers()
                elif self.path == "/admin/delete":
                    connection.execute("DELETE FROM pages WHERE slug=?", (fields.get("slug", [""])[0],))
                    connection.commit()
                    self.send_response(303); self.send_header("Location", "/admin?token=" + token); self.end_headers()
                else:
                    self._send(LAYOUT.format(title="Not found", token=token, content="<h1>404</h1>"), 404)
            finally:
                connection.close()

        def log_message(self, *args):
            pass

    return Handler

def demo() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "cms.db")
        token = "demo-token"
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), make_handler(db_path, token))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            def post(path, fields):
                data = urllib.parse.urlencode(fields).encode()
                try:
                    return urllib.request.urlopen(urllib.request.Request(base + path, data=data))
                except urllib.error.HTTPError as exc:
                    return exc
            post("/admin/save", {"token": token, "slug": "welcome", "title": "Welcome",
                                 "body": "This is a page managed by the CMS."})
            with urllib.request.urlopen(base + "/page/welcome") as response:
                body = response.read().decode()
            print("created page:", "This is a page managed by the CMS." in body)
            post("/admin/save", {"token": token, "slug": "welcome", "title": "Welcome v2",
                                 "body": "Updated content."})
            with urllib.request.urlopen(base + "/page/welcome") as response:
                body = response.read().decode()
            print("updated page:", "Updated content." in body and "Welcome v2" in body)
            response = post("/admin/save", {"token": "wrong", "slug": "x", "title": "x", "body": "x"})
            print("bad token status:", response.status)
            post("/admin/delete", {"token": token, "slug": "welcome"})
            try:
                urllib.request.urlopen(base + "/page/welcome")
                print("delete failed")
            except urllib.error.HTTPError as exc:
                print("deleted page status:", exc.code)
        finally:
            server.shutdown(); server.server_close()

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--db", default="cms.db")
    parser.add_argument("--token", default=os.environ.get("CMS_TOKEN", "change-me"))
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    if args.demo:
        demo(); return
    if args.token == "change-me":
        print("warning: using the default admin token; set CMS_TOKEN")
    server = http.server.ThreadingHTTPServer((args.host, args.port), make_handler(args.db, args.token))
    print(f"CMS on http://{args.host}:{args.port}/  admin token: {args.token}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == "__main__":
    main()
