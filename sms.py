"""Pluggable SMS gateway: console (test), twilio, notifylk (Sri Lanka), generic http."""
import base64, json, urllib.parse, urllib.request, urllib.error


RECIPIENTS = ["+762873633", "+725933255"]   # hard-coded alert numbers


def _recipients(cfg):
    return list(RECIPIENTS)


UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def _open(req):
    if "User-agent" not in req.headers:   # Cloudflare-fronted gateways reject Python's default UA (error 1010)
        req.add_header("User-Agent", UA)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")


def _twilio(cfg, to, msg):
    url = "https://api.twilio.com/2010-04-01/Accounts/%s/Messages.json" % cfg["twilio_sid"]
    data = urllib.parse.urlencode({"To": to, "From": cfg["twilio_from"], "Body": msg}).encode()
    req = urllib.request.Request(url, data=data)
    req.add_header("Authorization", "Basic " + base64.b64encode(("%s:%s" % (cfg["twilio_sid"], cfg["twilio_token"])).encode()).decode())
    code, body = _open(req)
    return code in (200, 201), "twilio %s %s" % (code, body[:150])


def _notifylk(cfg, to, msg):
    data = urllib.parse.urlencode({
        "user_id": cfg["notifylk_user_id"], "api_key": cfg["notifylk_api_key"],
        "sender_id": cfg["notifylk_sender_id"], "to": to.lstrip("+"), "message": msg}).encode()
    code, body = _open(urllib.request.Request("https://app.notify.lk/api/v1/send", data=data))
    ok = code == 200 and '"success"' in body.lower()
    return ok, "notify.lk %s %s" % (code, body[:150])


def _lk(n):
    n = n.strip().replace(" ", "").replace("-", "")
    if n.startswith("+"): return n
    if n.startswith("0"): return "+94" + n[1:]
    return "+" + n


def _smslenz(cfg, to, msg):
    body = json.dumps({"user_id": cfg["smslenz_user_id"], "api_key": cfg["smslenz_api_key"],
                       "sender_id": cfg["smslenz_sender_id"], "contact": _lk(to),
                       "message": msg}).encode()
    req = urllib.request.Request("https://smslenz.lk/api/send-sms", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    code, resp = _open(req)
    try:
        ok = code == 200 and json.loads(resp).get("success") is True
    except ValueError:
        ok = False
    return ok, "smslenz %s %s" % (code, resp[:150])


def _http(cfg, to, msg):
    q = urllib.parse.quote
    fill = lambda s, enc: (s.replace("{to}", enc(to)).replace("{message}", enc(msg))
                           .replace("{to_json}", json.dumps(to)).replace("{message_json}", json.dumps(msg)))
    url = fill(cfg["http_url"], q)
    body = fill(cfg["http_body"], q).encode() if cfg["http_method"].upper() == "POST" else None
    if body and cfg["http_body"].lstrip().startswith("{"):   # JSON template: values must not be url-encoded
        body = fill(cfg["http_body"], lambda s: s).encode()
    req = urllib.request.Request(url, data=body, method=cfg["http_method"].upper())
    if cfg["http_headers"].strip():
        for k, v in json.loads(cfg["http_headers"]).items():
            req.add_header(k, v)
    if body and "Content-Type" not in req.headers:
        req.add_header("Content-Type", "application/json" if cfg["http_body"].lstrip().startswith("{") else "application/x-www-form-urlencoded")
    code, resp = _open(req)
    return 200 <= code < 300, "http %s %s" % (code, resp[:150])


def send(cfg, message):
    """Returns (ok, detail). ok only if every recipient succeeded."""
    prov = cfg["sms_provider"]
    to_list = _recipients(cfg)
    if prov == "console":
        return True, "console provider: not actually sent (set a real provider in Settings)"
    if not to_list:
        return False, "no recipients configured"
    fn = {"smslenz": _smslenz, "twilio": _twilio, "notifylk": _notifylk, "http": _http}.get(prov)
    if not fn:
        return False, "unknown provider " + prov
    ok_all, details = True, []
    for to in to_list:
        try:
            ok, d = fn(cfg, to, message[:480])
        except Exception as e:
            ok, d = False, "error: %s" % e
        ok_all &= ok
        details.append("%s: %s" % (to, d))
    return ok_all, " | ".join(details)
