"""Tiny JSON API + static file server (standard library only)."""
import csv, io, json, mimetypes, os, time, urllib.parse
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import auth, competitors, config, db, monitor, reports, sms
from config import BASE

STATIC = os.path.join(BASE, "static")


def rng(qs):
    if qs.get("from"):
        a = date.fromisoformat(qs["from"][0])
        b = date.fromisoformat(qs.get("to", [qs["from"][0]])[0])
        return a, max(a, b)
    p = qs.get("period", ["7d"])[0]
    if p == "all":
        c = db.conn()
        d = c.execute("SELECT MIN(day) d FROM daily_stats").fetchone()["d"]
        c.close()
        return (date.fromisoformat(d) if d else date.today()), date.today()
    return reports.resolve_period(p) or reports.resolve_period("7d")


def csv_bytes(header, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode()


class H(BaseHTTPRequestHandler):
    server_version = "VBA/1.0"

    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def ip(self):
        return self.headers.get("X-Real-IP") or self.client_address[0]

    def authed(self):
        ck = self.headers.get("Cookie", "")
        for part in ck.split(";"):
            k, _, v = part.strip().partition("=")
            if k == auth.COOKIE:
                return auth.valid_token(v)
        return False

    def cookie(self, val, maxage):
        sec = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
        return "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d%s" % (auth.COOKIE, val, maxage, sec)

    def login_page(self, err="", code=200):
        self.send(code, auth.LOGIN_HTML.replace("__ERR__", err).encode(), "text/html; charset=utf-8")

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        path = u.path
        try:
            if path == "/logout":
                return self.send(302, b"", "text/plain", {"Location": "/login", "Set-Cookie": self.cookie("", 0)})
            if path == "/login":
                if self.authed():
                    return self.send(302, b"", "text/plain", {"Location": "/"})
                return self.login_page()
            if not self.authed():
                if path.startswith("/api/"):
                    return self.send(401, {"error": "login required"})
                return self.send(302, b"", "text/plain", {"Location": "/login"})
            if path.startswith("/api/"):
                return self.api_get(path[5:], qs)
            f = os.path.normpath(os.path.join(STATIC, "index.html" if path == "/" else path.lstrip("/")))
            if not f.startswith(STATIC) or not os.path.isfile(f):
                return self.send(404, b"not found", "text/plain")
            self.send(200, open(f, "rb").read(), mimetypes.guess_type(f)[0] or "application/octet-stream")
        except Exception as e:
            self.send(500, {"error": str(e)})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) or b""
            if u.path == "/login":
                ip = self.ip()
                if auth.blocked(ip):
                    return self.login_page("Too many attempts. Try again in 15 minutes.", 429)
                f = urllib.parse.parse_qs(raw.decode("utf-8", "ignore"))
                ok = auth.check_credentials(f.get("username", [""])[0], f.get("password", [""])[0])
                auth.record(ip, ok)
                if not ok:
                    return self.login_page("Wrong username or password.", 401)
                return self.send(302, b"", "text/plain", {"Location": "/", "Set-Cookie": self.cookie(auth.make_token(), auth.SESSION_SEC)})
            if not self.authed():
                return self.send(401, {"error": "login required"})
            data = json.loads(raw or b"{}")
            if self.headers.get("Origin") and urllib.parse.urlparse(self.headers["Origin"]).netloc != self.headers.get("Host"):
                return self.send(403, {"error": "bad origin"})   # blocks cross-site POSTs
            self.api_post(u.path[5:], data)
        except Exception as e:
            self.send(500, {"error": str(e)})

    # ------------------------------------------------------------------ GET
    def api_get(self, name, qs):
        c = db.conn()
        try:
            if name == "overview":
                return self.send(200, reports.overview(c))
            if name == "report":
                a, b = rng(qs)
                return self.send(200, reports.report(c, a, b))
            if name == "monthly":
                return self.send(200, reports.monthly(c))
            if name == "videos":
                g = lambda k, d="": qs.get(k, [d])[0]
                return self.send(200, reports.videos(c, g("q"), g("sort", "views"), g("tag"), g("performer")))
            if name.startswith("video/"):
                d = reports.video_detail(c, name[6:])
                return self.send(200 if d else 404, d or {"error": "not found"})
            if name == "insights":
                return self.send(200, reports.insights(c))
            if name == "alerts":
                return self.send(200, [dict(r) for r in c.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT 100")])
            if name == "status":
                now = int(time.time())
                lu = reports.last_upload(c)
                cfg = config.load()
                sc = c.execute("SELECT * FROM scrapes ORDER BY id DESC LIMIT 30").fetchall()
                return self.send(200, dict(
                    now=now, last_upload=lu, idle_sec=(now - lu) if lu else None, limit_sec=cfg["alert_after_minutes"] * 60,
                    stalled=db.kv_get(c, "stalled", "0") == "1", last_ok=int(db.kv_get(c, "last_ok", 0)),
                    fail_streak=int(db.kv_get(c, "fail_streak", 0)), next_poll=monitor.state["next_poll"],
                    running=monitor.state["running"], sms_provider=cfg["sms_provider"],
                    scrapes=[dict(r) for r in sc]))
            if name == "competitors":
                return self.send(200, competitors.leaderboard(c))
            if name == "competitor-insights":
                return self.send(200, competitors.insights(c))
            if name.startswith("competitor/"):
                d = competitors.detail(c, urllib.parse.unquote(name[11:]))
                return self.send(200 if d else 404, d or {"error": "not found"})
            if name == "export/competitors.csv":
                head, rows = competitors.csv_rows(c)
                return self.send(200, csv_bytes(head, rows), "text/csv", {"Content-Disposition": "attachment; filename=competitors.csv"})
            if name == "settings":
                return self.send(200, config.public(config.load()))
            if name == "export/videos.csv":
                rows = reports.videos(c, sort="new")
                body = csv_bytes(["id", "title", "uploaded_at", "views", "likes", "orgasms", "playlists", "comments", "gain24h", "engagement_pct", "url"],
                                 [[r["id"], r["title"], datetime.fromtimestamp(r["uploaded_at"]).isoformat(" "), r["views"], r["likes"], r["orgasms"], r["playlists"], r["comments"], r["gain24"], r["engagement"], r["url"]] for r in rows])
                return self.send(200, body, "text/csv", {"Content-Disposition": "attachment; filename=videos.csv"})
            if name == "export/report.csv":
                a, b = rng(qs)
                r = reports.report(c, a, b)
                body = csv_bytes(["day", "uploads", "views_gained", "likes_gained", "orgasms_gained", "comments_gained"],
                                 [[s["day"], s["uploads"], s["views"], s["likes"], s["orgasms"], s["comments"]] for s in r["series"]])
                return self.send(200, body, "text/csv", {"Content-Disposition": "attachment; filename=report_%s_%s.csv" % (a, b)})
            return self.send(404, {"error": "unknown endpoint"})
        finally:
            c.close()

    # ----------------------------------------------------------------- POST
    def api_post(self, name, data):
        if name == "scrape":
            return self.send(200, monitor.scrape_now(full=bool(data.get("full", True))))
        if name == "comp-scrape":
            return self.send(200, dict(started=competitors.run_async()))
        if name == "check":
            cfg = config.load()
            return self.send(200, {"result": monitor.check_stall(cfg, force=bool(data.get("force")))})
        if name == "settings":
            config.save(data)
            monitor._wake.set()
            competitors._wake.set()
            return self.send(200, config.public(config.load()))
        if name == "test-sms":
            cfg = config.load()
            ok, detail = sms.send(cfg, "SP Bot: test message. SMS gateway works!")
            c = db.conn()
            c.execute("INSERT INTO alerts(ts,kind,message,sms_ok,detail) VALUES(?,?,?,?,?)", (int(time.time()), "test", "Test SMS", int(ok), detail))
            c.commit(); c.close()
            return self.send(200, dict(ok=ok, detail=detail))
        return self.send(404, {"error": "unknown endpoint"})


def serve(cfg):
    if not auth.configured():
        raise SystemExit("Set VBA_AUTH_USER and VBA_AUTH_PASS environment variables (login credentials).")
    srv = ThreadingHTTPServer((cfg["host"], cfg["port"]), H)
    print("Dashboard: http://%s:%d" % (cfg["host"], cfg["port"]))
    srv.serve_forever()
