"""Config handling: defaults + config.json overrides (editable from the Settings page)."""
import json, os, threading

BASE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(BASE, "config.json")
_lock = threading.Lock()

DEFAULTS = {
    # --- site / scraping ---
    "site": "https://sxyprn.com",
    "username": "drvector",
    "poll_minutes": 5,            # quick check of newest page (upload detection)
    "full_crawl_minutes": 60,     # crawl every page to refresh views/likes
    "max_pages": 60,              # safety cap (20 videos per page)
    "request_delay_sec": 1.5,     # politeness delay between page requests
    # --- competitor tracking (top bloggers of /community) ---
    "comp_enabled": True,
    "comp_top_n": 20,             # how many top community bloggers to track
    "comp_poll_minutes": 60,      # how often to re-crawl competitors
    "comp_min_pages": 2,          # always read at least this many pages per blogger (20 videos each)
    "comp_max_pages": 12,        # ...and keep reading until videos are older than comp_window_hours
    "comp_window_hours": 48,
    "comp_extra": "",             # extra blogger handles to track, comma separated (e.g. AJ47,Shizuka)
    "comp_exclude": "",           # handles to ignore, comma separated
    # --- upload monitor ---
    "alert_after_minutes": 30,    # alert if no new upload for this long
    "repeat_minutes": 120,        # re-send while still stalled (0 = only once)
    "notify_recovery": False,      # SMS when uploading resumes
    "quiet_enabled": False,       # don't alert during quiet hours (e.g. sleeping)
    "quiet_start": "00:00",
    "quiet_end": "07:00",
    "alert_on_scrape_fail": False, # SMS if the site can't be reached 3+ times in a row
    # --- daily summary SMS ---
    "daily_summary": False,
    "daily_summary_hour": 21,
    # --- SMS gateway ---
    "sms_provider": "console",    # console | smslenz | twilio | notifylk | http
    "sms_recipients": "",         # comma separated, international format e.g. +94771234567
    "twilio_sid": "", "twilio_token": "", "twilio_from": "",
    "notifylk_user_id": "", "notifylk_api_key": "", "notifylk_sender_id": "NotifyDEMO",
    "smslenz_user_id": "", "smslenz_api_key": "", "smslenz_sender_id": "SMSlenzDEMO",
    "http_method": "GET",
    "http_url": "https://example.com/send?to={to}&text={message}",
    "http_body": "",              # for POST, e.g. to={to}&text={message}  (or JSON using {to_json}/{message_json})
    "http_headers": "",           # JSON object string, e.g. {"Authorization": "Bearer xxx"}
    # --- dashboard server ---
    "host": "127.0.0.1",
    "port": 8765,
}
SECRETS = ("twilio_token", "notifylk_api_key", "smslenz_api_key", "http_headers")


def load():
    cfg = dict(DEFAULTS)
    with _lock:
        if os.path.exists(PATH):
            try:
                cfg.update(json.load(open(PATH)))
            except Exception:
                pass
    for k in DEFAULTS:  # env overrides, e.g. VBA_TWILIO_TOKEN
        v = os.environ.get("VBA_" + k.upper())
        if v is not None:
            cfg[k] = type(DEFAULTS[k])(v) if not isinstance(DEFAULTS[k], bool) else v.lower() in ("1", "true", "yes")
    return cfg


def save(new):
    cur = load()
    for k, v in new.items():
        if k not in DEFAULTS:
            continue
        if k in SECRETS and v == "********":
            continue
        d = DEFAULTS[k]
        try:
            if isinstance(d, bool):
                v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
            elif isinstance(d, int):
                v = int(v)
            elif isinstance(d, float):
                v = float(v)
            else:
                v = str(v)
        except (ValueError, TypeError):
            continue
        cur[k] = v
    with _lock:
        tmp = PATH + ".tmp"
        json.dump(cur, open(tmp, "w"), indent=2)
        os.replace(tmp, PATH)
        try:
            os.chmod(PATH, 0o600)
        except OSError:
            pass
    return cur


def public(cfg):
    out = dict(cfg)
    for k in SECRETS:
        if out.get(k):
            out[k] = "********"
    return out
