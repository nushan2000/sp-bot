"""Upload-stall detection, alerts and the background scheduler."""
import threading, time, traceback
from datetime import date, datetime, timedelta
import config, db, reports, scraper, sms

_wake = threading.Event()
_busy = threading.Lock()
state = dict(running=False, next_poll=0)


def ago(sec):
    sec = int(sec)
    if sec < 3600: return "%d min" % (sec // 60)
    if sec < 86400: return "%dh %02dm" % (sec // 3600, sec % 3600 // 60)
    return "%dd %dh" % (sec // 86400, sec % 86400 // 3600)


def notify(c, cfg, kind, msg):
    ok, detail = sms.send(cfg, msg)
    c.execute("INSERT INTO alerts(ts,kind,message,sms_ok,detail) VALUES(?,?,?,?,?)", (int(time.time()), kind, msg, int(ok), detail))
    c.commit()
    print("[ALERT:%s] %s -> %s (%s)" % (kind, msg, "sent" if ok else "FAILED", detail))
    return ok


def in_quiet(cfg, now):
    if not cfg["quiet_enabled"]:
        return False
    cur = datetime.fromtimestamp(now).strftime("%H:%M")
    a, b = cfg["quiet_start"], cfg["quiet_end"]
    return (a <= cur < b) if a <= b else (cur >= a or cur < b)


def check_stall(cfg, force=False):
    now = int(time.time())
    c = db.conn()
    try:
        lu = reports.last_upload(c)
        if lu is None:
            return "no videos yet"
        idle = now - lu
        last_ok = int(db.kv_get(c, "last_ok", 0))
        if not force and now - last_ok > max(900, cfg["poll_minutes"] * 180):
            return "site unreachable - cannot judge uploads"
        stalled = db.kv_get(c, "stalled", "0") == "1"
        limit = cfg["alert_after_minutes"] * 60
        last_alert = int(db.kv_get(c, "last_alert", 0))
        if idle >= limit:
            if in_quiet(cfg, now):
                return "stalled but in quiet hours"
            due = (not stalled) or (cfg["repeat_minutes"] > 0 and now - last_alert >= cfg["repeat_minutes"] * 60)
            if due or force:
                msg = "SP Bot ALERT: no new upload for %s (last: %s). Please check uploads." % (
                    ago(idle), datetime.fromtimestamp(lu).strftime("%d %b %H:%M"))
                notify(c, cfg, "stall", msg)
                db.kv_set(c, "stalled", 1); db.kv_set(c, "last_alert", now); c.commit()
                return "alert sent"
            return "stalled, alert already sent"
        if stalled:
            if cfg["notify_recovery"]:
                notify(c, cfg, "recovered", "SP Bot: uploads resumed. New video %s ago (was idle before)." % ago(idle))
            db.kv_set(c, "stalled", 0); c.commit()
            return "recovered"
        return "ok (idle %s)" % ago(idle)
    finally:
        c.close()


def check_scrape_health(cfg):
    c = db.conn()
    try:
        streak = int(db.kv_get(c, "fail_streak", 0))
        flagged = db.kv_get(c, "fail_alerted", "0") == "1"
        if streak >= 3 and not flagged and cfg["alert_on_scrape_fail"] and not in_quiet(cfg, int(time.time())):
            notify(c, cfg, "scrape_fail", "SP Bot: cannot read the site (%d failed checks in a row). Upload monitoring is blind." % streak)
            db.kv_set(c, "fail_alerted", 1)
        elif streak == 0 and flagged:
            db.kv_set(c, "fail_alerted", 0)
        c.commit()
    finally:
        c.close()


def daily_summary(cfg):
    if not cfg["daily_summary"]:
        return
    now = datetime.now()
    c = db.conn()
    try:
        today = date.today().isoformat()
        if now.hour >= cfg["daily_summary_hour"] and db.kv_get(c, "summary_day") != today:
            notify(c, cfg, "summary", reports.sms_summary(c, date.today()))
            db.kv_set(c, "summary_day", today); c.commit()
    finally:
        c.close()


def maintenance():
    c = db.conn()
    try:
        c.execute("DELETE FROM snapshots WHERE ts < ?", (int(time.time()) - 30 * 86400,))
        c.execute("DELETE FROM scrapes WHERE ts < ?", (int(time.time()) - 14 * 86400,))
        c.commit()
    finally:
        c.close()


def scrape_now(full=False):
    """Run one scrape + stall check. Safe to call from request threads."""
    if not _busy.acquire(blocking=False):
        return dict(ok=False, error="a scrape is already running")
    try:
        state["running"] = True
        cfg = config.load()
        res = scraper.run(cfg, full)
        check_scrape_health(cfg)
        if res["ok"]:
            check_stall(cfg)
        return res
    finally:
        state["running"] = False
        _busy.release()


def loop():
    last_maint = 0
    while True:
        try:
            cfg = config.load()
            c = db.conn()
            last_poll = int(db.kv_get(c, "last_poll", 0))
            last_full = int(db.kv_get(c, "last_full", 0))
            c.close()
            now = int(time.time())
            if now - last_poll >= cfg["poll_minutes"] * 60:
                full = now - last_full >= cfg["full_crawl_minutes"] * 60
                c = db.conn(); db.kv_set(c, "last_poll", now); c.commit(); c.close()
                res = scrape_now(full)
                print(time.strftime("%H:%M:%S"), "scrape", "full" if full else "quick", res)
            else:  # keep stall detection ticking between scrapes (e.g. alert repeats)
                check_stall(cfg)
            daily_summary(cfg)
            if now - last_maint > 6 * 3600:
                maintenance(); last_maint = now
            c = db.conn(); state["next_poll"] = int(db.kv_get(c, "last_poll", 0)) + cfg["poll_minutes"] * 60; c.close()
        except Exception:
            traceback.print_exc()
        _wake.wait(30)
        _wake.clear()


def start():
    t = threading.Thread(target=loop, daemon=True, name="scheduler")
    t.start()
    return t
