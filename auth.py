"""Simple username/password login with signed session cookies (standard library only).
Credentials come from env vars VBA_AUTH_USER / VBA_AUTH_PASS (see deploy/vba.env)."""
import hashlib, hmac, os, secrets, time
from config import BASE

SESSION_SEC = 7 * 86400
COOKIE = "vba_session"
_fails = {}   # ip -> [count, first_ts]  (simple brute-force throttle)

USER = os.environ.get("VBA_AUTH_USER", "")
PASS = os.environ.get("VBA_AUTH_PASS", "")


def _secret():
    p = os.path.join(BASE, "data", ".session_secret")
    if not os.path.exists(p):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(secrets.token_bytes(32))
        os.chmod(p, 0o600)
    return open(p, "rb").read()


def configured():
    return bool(USER and PASS)


def check_credentials(u, p):
    ok1 = hmac.compare_digest(u.encode(), USER.encode())
    ok2 = hmac.compare_digest(p.encode(), PASS.encode())
    return ok1 and ok2


def make_token():
    exp = str(int(time.time()) + SESSION_SEC)
    sig = hmac.new(_secret(), (USER + "|" + exp).encode(), hashlib.sha256).hexdigest()
    return exp + "." + sig


def valid_token(tok):
    try:
        exp, sig = tok.split(".", 1)
        good = hmac.new(_secret(), (USER + "|" + exp).encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, good) and int(exp) > time.time()
    except Exception:
        return False


def blocked(ip):
    f = _fails.get(ip)
    if not f:
        return False
    if time.time() - f[1] > 900:
        _fails.pop(ip, None)
        return False
    return f[0] >= 5


def record(ip, ok):
    if ok:
        _fails.pop(ip, None)
    else:
        f = _fails.setdefault(ip, [0, time.time()])
        f[0] += 1


LOGIN_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Login - SP Bot</title>
<style>
:root{--bg:#f5f6f8;--card:#fff;--ink:#1d2129;--mut:#6b7280;--line:#e5e7eb;--pri:#2563eb;--bad:#dc2626}
@media(prefers-color-scheme:dark){:root{--bg:#111318;--card:#1b1e26;--ink:#e6e8ee;--mut:#9aa1b0;--line:#2b303b;--pri:#60a5fa;--bad:#f87171}}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,sans-serif;padding:16px}
form{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:24px;width:100%;max-width:340px;display:flex;flex-direction:column;gap:12px}
h1{font-size:18px;margin:0 0 4px}label{display:flex;flex-direction:column;gap:3px;color:var(--mut);font-size:12px}
input{background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:9px 10px;font:inherit}
button{background:var(--pri);color:#fff;border:0;border-radius:6px;padding:10px;cursor:pointer;font:inherit;font-weight:600}
.err{color:var(--bad);font-size:13px;min-height:1em}
</style></head><body>
<form method="post" action="/login">
<h1>📊 SP Bot</h1>
<label>Username<input name="username" autocomplete="username" autofocus required></label>
<label>Password<input name="password" type="password" autocomplete="current-password" required></label>
<div class="err">__ERR__</div>
<button type="submit">Sign in</button>
</form></body></html>"""
