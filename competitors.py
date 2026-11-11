"""Competitor tracking: the top bloggers of <site>/community, their upload pace, views, tags and rank moves.

Everything comes from public pages:
  /community/<offset>.html   12 bloggers per page, ordered by stars then likes  -> rank, posts, vids, lnks, likes
  /blog/@<name>/<offset>.html  20 videos per page                               -> upload times, views, likes, tags
The site publishes no per-blogger traffic, so "traffic" here = views the blogger's recent videos gain (a lower bound).
"""
import html as H, json, re, threading, time, traceback, urllib.parse
from collections import Counter, defaultdict
from datetime import datetime
import config, db, scraper

PER_PAGE = 12
state = dict(running=False, progress="", last_error="", next_run=0)
_lock = threading.Lock()
_wake = threading.Event()


# ----------------------------------------------------------------------------- parsing
def parse_community(page, offset=0):
    out = []
    for href, b in re.findall(r"<a href='(/(?:@[^']+|blog/[^']+))'><div class='blogger_el'>(.*?)</div></div></a>", page, re.S):
        nm = re.search(r"class='a_name'[^>]*>(.*?)</span>", b, re.S)
        name = H.unescape(re.sub(r"<[^>]+>", "", nm.group(1))).strip() if nm else href
        if href.startswith("/@"):
            handle = urllib.parse.unquote(href[2:]).strip("/")
            base = "/blog/@" + handle
        else:
            handle = href.split("/")[2]
            base = "/blog/" + handle
        num = lambda lab: int((re.search(lab + r":\s*<b>(\d+)</b>", b) or [0, 0])[1])
        ch = re.search(r"style='color:(#[0-9A-Fa-f]{3,8});background-color:(#[0-9A-Fa-f]{3,8})[^']*' class='a_char", b)
        out.append(dict(handle=handle, name=name, base=base, rank=offset + len(out) + 1,
                        stars=len(re.findall(r"class='stars'", b)),
                        posts=num("Posts"), vids=num("Vids"), lnks=num("Lnks"), likes=num("Likes"),
                        fg=ch.group(1) if ch else "#fff", bg=ch.group(2) if ch else "#444"))
    return out


def fetch_community(cfg, n):
    site, rows = cfg["site"].rstrip("/"), []
    for off in range(0, n, PER_PAGE):
        rows += parse_community(scraper.fetch("%s/community/%d.html" % (site, off)), off)
        if off + PER_PAGE < n:
            time.sleep(cfg["request_delay_sec"])
    return rows[:n]


def crawl_blogger(cfg, base):
    """Newest videos of one blogger. Reads min_pages, then more until the window is covered or max_pages."""
    now = int(time.time())
    posts, seen = [], set()
    for i in range(max(1, cfg["comp_max_pages"])):
        page = scraper.fetch("%s%s/%d.html" % (cfg["site"].rstrip("/"), base, i * 20))
        got = [p for p in scraper.parse_page(page, now) if p["id"] not in seen]
        if not got:
            break
        for p in got:
            seen.add(p["id"])
            posts.append(p)
        if "rel='next'" not in page and 'rel="next"' not in page:
            break
        covered = i + 1 >= cfg["comp_min_pages"] and min(p["uploaded_at"] for p in got) < now - cfg["comp_window_hours"] * 3600
        if covered:
            break
        time.sleep(cfg["request_delay_sec"])
    return posts


def _csv(s):
    return [x.strip().lstrip("@") for x in (s or "").split(",") if x.strip()]


# ----------------------------------------------------------------------------- storing
def _event(c, ts, handle, kind, msg):
    c.execute("INSERT INTO comp_events(ts,handle,kind,message) VALUES(?,?,?,?)", (ts, handle, kind, msg))


def save_community(c, rows, now, top_n):
    old = {r["handle"]: r for r in c.execute("SELECT * FROM comp_bloggers WHERE extra=0")}
    first_run = not old
    keep = set()
    for r in rows:
        keep.add(r["handle"])
        o = old.get(r["handle"])
        if o is None:
            if not first_run:
                _event(c, now, r["handle"], "enter", "%s entered the top %d at #%d" % (r["name"], top_n, r["rank"]))
            c.execute("INSERT INTO comp_bloggers(handle,name,base,rank,stars,posts,vids,lnks,likes,fg,bg,extra,active,first_seen,last_seen) VALUES(?,?,?,?,?,?,?,?,?,?,?,0,1,?,?)",
                      (r["handle"], r["name"], r["base"], r["rank"], r["stars"], r["posts"], r["vids"], r["lnks"], r["likes"], r["fg"], r["bg"], now, now))
        else:
            if o["active"] and o["rank"] and r["rank"] != o["rank"]:
                d = o["rank"] - r["rank"]
                if abs(d) >= 2 or r["rank"] <= 3:
                    _event(c, now, r["handle"], "rank", "%s %s %d place%s: #%d -> #%d" % (r["name"], "climbed" if d > 0 else "dropped", abs(d), "" if abs(d) == 1 else "s", o["rank"], r["rank"]))
            c.execute("UPDATE comp_bloggers SET name=?,base=?,rank=?,stars=?,posts=?,vids=?,lnks=?,likes=?,fg=?,bg=?,active=1,last_seen=? WHERE handle=?",
                      (r["name"], r["base"], r["rank"], r["stars"], r["posts"], r["vids"], r["lnks"], r["likes"], r["fg"], r["bg"], now, r["handle"]))
        c.execute("INSERT INTO comp_hist VALUES(?,?,?,?,?,?,?)", (r["handle"], now, r["rank"], r["posts"], r["vids"], r["lnks"], r["likes"]))
    for h, o in old.items():
        if h not in keep and o["active"]:
            _event(c, now, h, "leave", "%s dropped out of the top %d (was #%d)" % (o["name"], top_n, o["rank"] or 0))
            c.execute("UPDATE comp_bloggers SET active=0 WHERE handle=?", (h,))


def save_posts(c, handle, posts, now):
    new = 0
    for p in posts:
        old = c.execute("SELECT views,likes,orgasms,comments FROM comp_posts WHERE id=?", (p["id"],)).fetchone()
        tags = ",".join(sorted(set(p["tags"])))
        perf = "|".join(sorted(set(p["performers"])))
        age = now - p["uploaded_at"]
        exact = 1 if age < 23 * 3600 else 0          # older videos only show "N days ago" => hour of day unknown
        m = (p["views"], p["likes"], p["orgasms"], p["comments"])
        if old is None:
            new += 1
            c.execute("INSERT INTO comp_posts(id,handle,title,thumb,uploaded_at,exact,first_seen,last_seen,views,likes,orgasms,comments,tags,performers) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (p["id"], handle, p["title"], p["thumb"], p["uploaded_at"], exact, now, now) + m + (tags, perf))
            c.execute("INSERT INTO comp_snaps VALUES(?,?,?,?)", (p["id"], now, p["views"], p["likes"]))
        else:
            c.execute("UPDATE comp_posts SET last_seen=?,views=?,likes=?,orgasms=?,comments=?,tags=?,performers=?,title=? WHERE id=?",
                      (now,) + m + (tags, perf, p["title"], p["id"]))   # uploaded_at is kept from first sight (most precise)
            last = c.execute("SELECT ts,views FROM comp_snaps WHERE post_id=? ORDER BY ts DESC LIMIT 1", (p["id"],)).fetchone()
            if not last or p["views"] != last["views"] and now - last["ts"] >= 3300:
                c.execute("INSERT INTO comp_snaps VALUES(?,?,?,?)", (p["id"], now, p["views"], p["likes"]))
    c.execute("UPDATE comp_bloggers SET last_crawl=? WHERE handle=?", (now, handle))
    return new


def run(cfg=None):
    """One full competitor refresh. Safe to call from any thread; returns a result dict."""
    cfg = cfg or config.load()
    if not _lock.acquire(blocking=False):
        return dict(ok=False, error="a competitor scrape is already running")
    state["running"] = True
    c = db.conn()
    try:
        now = int(time.time())
        excl = {x.lower() for x in _csv(cfg["comp_exclude"])}
        state["progress"] = "reading community page"
        rows = [r for r in fetch_community(cfg, max(1, int(cfg["comp_top_n"]))) if r["handle"].lower() not in excl]
        save_community(c, rows, now, cfg["comp_top_n"])
        targets = [(r["handle"], r["base"], r["name"]) for r in rows]
        have = {r[0].lower() for r in targets}
        for h in _csv(cfg["comp_extra"]):
            if h.lower() in have or h.lower() in excl:
                continue
            c.execute("INSERT OR IGNORE INTO comp_bloggers(handle,name,base,extra,active,first_seen,last_seen,fg,bg) VALUES(?,?,?,1,1,?,?,'#fff','#555')", (h, h, "/blog/@" + h, now, now))
            c.execute("UPDATE comp_bloggers SET active=1 WHERE handle=?", (h,))
            targets.append((h, "/blog/@" + h, h))
        c.commit()
        total_new, failed = 0, []
        for i, (h, base, name) in enumerate(targets):
            state["progress"] = "%d/%d  %s" % (i + 1, len(targets), name)
            try:
                total_new += save_posts(c, h, crawl_blogger(cfg, base), int(time.time()))
                c.commit()
            except Exception as e:
                c.rollback()
                failed.append("%s: %s" % (name, str(e)[:80]))
            time.sleep(cfg["request_delay_sec"])
        t = int(time.time())
        c.execute("DELETE FROM comp_hist WHERE ts<?", (t - 60 * 86400,))
        c.execute("DELETE FROM comp_snaps WHERE ts<?", (t - 14 * 86400,))
        c.execute("DELETE FROM comp_events WHERE ts<?", (t - 60 * 86400,))
        c.execute("DELETE FROM comp_posts WHERE last_seen<?", (t - 45 * 86400,))
        db.kv_set(c, "comp_last_ok", t)
        c.commit()
        state["last_error"] = "; ".join(failed)[:300]
        return dict(ok=True, bloggers=len(targets), new_videos=total_new, failed=failed)
    except Exception as e:
        c.rollback()
        state["last_error"] = str(e)[:300]
        return dict(ok=False, error=str(e))
    finally:
        state["running"] = False
        state["progress"] = ""
        c.close()
        _lock.release()


def run_async():
    if state["running"]:
        return False
    threading.Thread(target=run, daemon=True, name="comp-manual").start()
    return True


def loop():
    time.sleep(20)   # let the main upload monitor go first
    while True:
        try:
            cfg = config.load()
            c = db.conn()
            last = int(db.kv_get(c, "comp_last_ok", 0))
            c.close()
            due = last + cfg["comp_poll_minutes"] * 60
            if cfg["comp_enabled"] and time.time() >= due:
                print(time.strftime("%H:%M:%S"), "competitors", run(cfg))
                c = db.conn(); due = int(db.kv_get(c, "comp_last_ok", 0)) + cfg["comp_poll_minutes"] * 60; c.close()
            state["next_run"] = int(due)
        except Exception:
            traceback.print_exc()
        _wake.wait(60)
        _wake.clear()


def start():
    t = threading.Thread(target=loop, daemon=True, name="competitors")
    t.start()
    return t


# ----------------------------------------------------------------------------- metrics
def _median(v):
    v = sorted(v)
    return v[len(v) // 2] if v else 0


def _metrics(posts, now, window=None):
    """posts: dicts with uploaded_at, exact, views, likes, orgasms, comments, tags(list). Same maths for competitors and for you."""
    if window:
        posts = [p for p in posts if p["uploaded_at"] >= now - window]
        span = window
    else:
        span = max(6 * 3600, now - min((p["uploaded_at"] for p in posts), default=now))
    n = len(posts)
    mature = [p for p in posts if now - p["uploaded_at"] >= 86400] or posts   # fresh videos have not collected views yet
    views = [p["views"] for p in mature]
    tv = sum(views)
    hours = [0] * 24
    for p in posts:
        if p["exact"]:
            hours[datetime.fromtimestamp(p["uploaded_at"]).hour] += 1
    ups = sorted(p["uploaded_at"] for p in posts)
    gaps = [b - a for a, b in zip(ups, ups[1:])]
    tags = Counter(t for p in posts for t in p["tags"])
    perf = Counter(x for p in posts for x in p.get("performers", []))
    return dict(
        sample=n, capped=bool(posts) and not window and min(p["uploaded_at"] for p in posts) > now - 86400, src="sample",
        uploads_24h=sum(1 for p in posts if p["uploaded_at"] >= now - 86400),
        uploads_7d=sum(1 for p in posts if p["uploaded_at"] >= now - 7 * 86400) if span >= 7 * 86400 else None,
        per_day=round(n * 86400 / span, 1) if n > 1 else 0, last_upload=max((p["uploaded_at"] for p in posts), default=None),
        avg_views=round(tv / len(views)) if views else 0, median_views=_median(views), max_views=max((p["views"] for p in posts), default=0),
        avg_likes=round(sum(p["likes"] for p in mature) / len(mature), 1) if mature else 0,
        engagement=round(100 * sum(p["likes"] + p["orgasms"] for p in mature) / tv, 2) if tv else 0,
        avg_gap_min=round(sum(gaps) / len(gaps) / 60) if gaps else None,
        longest_gap_h=round(max(gaps) / 3600, 1) if gaps else None,
        hours=hours, top_tags=tags.most_common(8), top_performers=perf.most_common(5))


def _load_posts(c, handle=None):
    q = "SELECT * FROM comp_posts" + (" WHERE handle=?" if handle else "")
    out = defaultdict(list)
    for r in c.execute(q, (handle,) if handle else ()):
        d = dict(r)
        d["tags"] = [t for t in d["tags"].split(",") if t]
        d["performers"] = [t for t in d["performers"].split("|") if t]
        out[d["handle"]].append(d)
    return out


def _view_gain(c, ts):
    base = {r["post_id"]: r["views"] for r in c.execute(
        "SELECT s.post_id, s.views FROM comp_snaps s JOIN (SELECT post_id, MAX(ts) mt FROM comp_snaps WHERE ts<=? GROUP BY post_id) m ON s.post_id=m.post_id AND s.ts=m.mt", (ts,))}
    first = {r["post_id"]: r["views"] for r in c.execute(
        "SELECT s.post_id, s.views FROM comp_snaps s JOIN (SELECT post_id, MIN(ts) mt FROM comp_snaps GROUP BY post_id) m ON s.post_id=m.post_id AND s.ts=m.mt")}
    return base, first


def _hist_at(c, handle, ts):
    return c.execute("SELECT * FROM comp_hist WHERE handle=? AND ts<=? ORDER BY ts DESC LIMIT 1", (handle, ts)).fetchone() or \
        c.execute("SELECT * FROM comp_hist WHERE handle=? ORDER BY ts LIMIT 1", (handle,)).fetchone()


def _me(c, cfg, now):
    posts = []
    tg = defaultdict(list)
    for r in c.execute("SELECT post_id,name FROM post_tags WHERE kind='tag'"):
        tg[r["post_id"]].append(r["name"])
    for r in c.execute("SELECT * FROM posts WHERE active=1"):
        d = dict(r)
        d["exact"] = 1 if r["first_seen"] - r["uploaded_at"] < 23 * 3600 and now - r["uploaded_at"] < 60 * 86400 else 0
        d["tags"] = tg.get(r["id"], [])
        posts.append(d)
    m = _metrics(posts, now, window=14 * 86400) if posts else None
    if not m:
        return None
    m.update(name=cfg["username"], handle=cfg["username"], me=True, videos_total=len(posts),
             views_total=sum(p["views"] for p in posts), likes_total=sum(p["likes"] for p in posts))
    m["all_tags"] = Counter(t for p in posts for t in p["tags"])
    m["views_by_tag"] = defaultdict(list)
    for p in posts:
        for t in p["tags"]:
            m["views_by_tag"][t].append(p["views"])
    return m


def leaderboard(c, cfg=None):
    cfg = cfg or config.load()
    now = int(time.time())
    posts = _load_posts(c)
    base24, first = _view_gain(c, now - 86400)
    rows = []
    for b in c.execute("SELECT * FROM comp_bloggers WHERE active=1 ORDER BY extra, rank"):
        pl = posts.get(b["handle"], [])
        m = _metrics(pl, now) if pl else dict(sample=0, uploads_24h=0, uploads_7d=None, per_day=0, last_upload=None, avg_views=0, median_views=0,
                                              max_views=0, avg_likes=0, engagement=0, avg_gap_min=None, longest_gap_h=None, hours=[0] * 24, top_tags=[], top_performers=[])
        gain = sum(max(0, p["views"] - base24.get(p["id"], first.get(p["id"], p["views"]))) for p in pl)
        h24, h7 = _hist_at(c, b["handle"], now - 86400), _hist_at(c, b["handle"], now - 7 * 86400)
        span_h = round((now - h24["ts"]) / 3600, 1) if h24 else 0
        top_post = max(pl, key=lambda p: p["views"] / max(3600, now - p["uploaded_at"]) if now - p["uploaded_at"] < 2 * 86400 else -1, default=None)
        m.update(
            handle=b["handle"], name=b["name"], base=b["base"], rank=b["rank"], stars=b["stars"], extra=b["extra"], fg=b["fg"], bg=b["bg"],
            posts=b["posts"], vids=b["vids"], lnks=b["lnks"], likes=b["likes"],
            likes_per_post=round(b["likes"] / b["posts"], 1) if b["posts"] else 0, vid_share=round(100 * b["vids"] / b["posts"]) if b["posts"] else 0,
            rank_change=(h24["rank"] - b["rank"]) if h24 and h24["rank"] and b["rank"] and span_h >= 1 else None,
            posts_24h=(b["posts"] - h24["posts"]) if h24 and span_h >= 1 else None, likes_24h=(b["likes"] - h24["likes"]) if h24 and span_h >= 1 else None,
            posts_7d=(b["posts"] - h7["posts"]) if h7 and now - h7["ts"] >= 86400 else None, likes_7d=(b["likes"] - h7["likes"]) if h7 and now - h7["ts"] >= 86400 else None,
            hist_hours=span_h, views_gain_24h=gain, last_crawl=b["last_crawl"],
            idle_h=round((now - m["last_upload"]) / 3600, 1) if m["last_upload"] else None,
            hot=dict(id=top_post["id"], title=top_post["title"], views=top_post["views"]) if top_post and top_post["views"] else None)
        if m["posts_24h"] is not None and span_h >= 20:      # exact: net new posts on the community page over >=20h
            m["uploads_24h"] = round(m["posts_24h"] * 24 / span_h)
            m["capped"], m["src"] = False, "community"
            m["per_day"] = max(m["per_day"], m["uploads_24h"])
        m["best_hour"] = max(range(24), key=lambda i: m["hours"][i]) if sum(m["hours"]) else None
        rows.append(m)
    me = _me(c, cfg, now)
    if me:
        for k in ("all_tags", "views_by_tag"):
            me.pop(k)
    ok = int(db.kv_get(c, "comp_last_ok", 0))
    return dict(rows=rows, me=me, last_ok=ok, running=state["running"], progress=state["progress"], error=state["last_error"],
                next_run=state["next_run"], top_n=cfg["comp_top_n"], now=now)


def detail(c, handle):
    b = c.execute("SELECT * FROM comp_bloggers WHERE handle=?", (handle,)).fetchone()
    if not b:
        return None
    now = int(time.time())
    pl = _load_posts(c, handle).get(handle, [])
    m = _metrics(pl, now) if pl else {}
    hist = {}
    for r in c.execute("SELECT * FROM comp_hist WHERE handle=? AND ts>=? ORDER BY ts", (handle, now - 30 * 86400)):
        hist[datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d")] = dict(day=datetime.fromtimestamp(r["ts"]).strftime("%Y-%m-%d"), rank=r["rank"], posts=r["posts"], likes=r["likes"])
    hist = list(hist.values())
    for i, d in enumerate(hist):
        d["posts_gain"] = max(0, d["posts"] - hist[i - 1]["posts"]) if i else 0
        d["likes_gain"] = max(0, d["likes"] - hist[i - 1]["likes"]) if i else 0
    base, first = _view_gain(c, now - 86400)
    vids = []
    for p in sorted(pl, key=lambda p: -p["uploaded_at"])[:60]:
        age_h = max(1, (now - p["uploaded_at"]) / 3600)
        vids.append(dict(id=p["id"], title=p["title"], thumb=p["thumb"], url=cfg_site() + "/post/%s.html" % p["id"], uploaded_at=p["uploaded_at"], exact=p["exact"],
                         views=p["views"], likes=p["likes"], orgasms=p["orgasms"], comments=p["comments"], tags=p["tags"],
                         gain24=max(0, p["views"] - base.get(p["id"], first.get(p["id"], p["views"]))), vph=round(p["views"] / age_h, 1)))
    events = [dict(r) for r in c.execute("SELECT * FROM comp_events WHERE handle=? ORDER BY id DESC LIMIT 20", (handle,))]
    return dict(blogger=dict(b), metrics=m, history=hist, videos=vids, events=events, url=cfg_site() + (b["base"].replace("/blog/", "/@", 1) if "/@" in b["base"] else b["base"] + "/0.html"))


def cfg_site():
    return config.load()["site"].rstrip("/")


def insights(c, cfg=None):
    """Cross-competitor intelligence: trending videos, tag gaps vs you, best upload windows, activity feed."""
    cfg = cfg or config.load()
    now = int(time.time())
    posts = _load_posts(c)
    active = {r["handle"]: r["name"] for r in c.execute("SELECT handle,name FROM comp_bloggers WHERE active=1")}
    allp = [p for h, pl in posts.items() if h in active for p in pl]
    # trending now: views per hour among videos younger than 48h
    trend = []
    for p in allp:
        age = now - p["uploaded_at"]
        if 1800 <= age < 2 * 86400 and p["views"] > 0:
            trend.append(dict(id=p["id"], title=p["title"], thumb=p["thumb"], blogger=active[p["handle"]], handle=p["handle"], views=p["views"], likes=p["likes"],
                              vph=round(p["views"] / (age / 3600), 1), age_h=round(age / 3600, 1), tags=p["tags"][:5], url=cfg_site() + "/post/%s.html" % p["id"]))
    trend.sort(key=lambda x: -x["vph"])
    # tag gap analysis
    me = _me(c, cfg, now)
    mine = me["all_tags"] if me else Counter()
    mine_views = me["views_by_tag"] if me else {}
    mature = [p for p in allp if now - p["uploaded_at"] >= 86400] or allp
    ct = defaultdict(lambda: dict(videos=0, views=0, bloggers=set()))
    for p in mature:
        for t in p["tags"]:
            e = ct[t]; e["videos"] += 1; e["views"] += p["views"]; e["bloggers"].add(p["handle"])
    tags = []
    for t, e in ct.items():
        if e["videos"] < 3:
            continue
        mv = mine_views.get(t, [])
        tags.append(dict(tag=t, comp_videos=e["videos"], comp_bloggers=len(e["bloggers"]), comp_avg_views=round(e["views"] / e["videos"]),
                         my_videos=mine.get(t, 0), my_avg_views=round(sum(mv) / len(mv)) if mv else None))
    missing = sorted((t for t in tags if t["my_videos"] == 0), key=lambda t: -t["comp_avg_views"])[:15]
    shared = sorted((t for t in tags if t["my_videos"] > 0), key=lambda t: -t["comp_videos"])[:15]
    # upload hours: when competitors post vs when you post
    ch = [0] * 24
    for p in allp:
        if p["exact"]:
            ch[datetime.fromtimestamp(p["uploaded_at"]).hour] += 1
    mh = me["hours"] if me else [0] * 24
    ev = [dict(r, name=active.get(r["handle"], r["handle"])) for r in c.execute("SELECT * FROM comp_events ORDER BY id DESC LIMIT 40")]
    quiet = sorted(({"name": active[h], "handle": h, "idle_h": round((now - max(p["uploaded_at"] for p in pl)) / 3600, 1)} for h, pl in posts.items() if h in active and pl
                    and now - max(p["uploaded_at"] for p in pl) > 24 * 3600), key=lambda x: -x["idle_h"])
    return dict(trending=trend[:15], missing_tags=missing, shared_tags=shared, comp_hours=ch, my_hours=mh,
                events=ev, quiet=quiet, videos_sampled=len(allp))


def csv_rows(c):
    lb = leaderboard(c)
    head = ["rank", "name", "handle", "stars", "posts", "vids", "lnks", "likes", "likes_per_post", "posts_24h", "likes_24h", "uploads_24h", "uploads_per_day",
            "avg_views", "median_views", "views_gain_24h", "engagement_pct", "last_upload", "best_hour"]
    rows = [[r["rank"], r["name"], r["handle"], r["stars"], r["posts"], r["vids"], r["lnks"], r["likes"], r["likes_per_post"], r["posts_24h"], r["likes_24h"],
             r["uploads_24h"], r["per_day"], r["avg_views"], r["median_views"], r["views_gain_24h"], r["engagement"],
             datetime.fromtimestamp(r["last_upload"]).isoformat(" ") if r["last_upload"] else "", r["best_hour"]] for r in lb["rows"]]
    return head, rows
