"""Scrapes the public blog page of the account and stores posts + metrics."""
import html as H, re, time, urllib.request, urllib.error
from datetime import datetime
import db

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
UNIT = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800, "month": 2592000, "year": 31536000}


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "ignore")


def parse_rel(s, now):
    """'10 seconds ago', 'Hour ago', '2 days ago', 'Last year', 'Yesterday' -> unix ts (approximate for coarse units)."""
    s = (s or "").strip().lower()
    if s in ("just now", "now"):
        return int(now)
    if s == "yesterday":
        return int(now - 86400)
    m = re.match(r"(?:(\d+|an?|last)\s*)?(second|minute|hour|day|week|month|year)s?(?:\s+ago)?$", s)
    if not m:
        return int(now - 365 * 86400)   # unknown wording => treat as old, never as "new upload"
    n = int(m.group(1)) if m.group(1) and m.group(1).isdigit() else 1
    return int(now - n * UNIT[m.group(2)])


def parse_count(s):
    s = (s or "0").strip().replace(",", "")
    m = re.match(r"([\d.]+)\s*([kKmM]?)", s)
    if not m:
        return 0
    n = float(m.group(1))
    return int(n * {"": 1, "k": 1e3, "m": 1e6}[m.group(2).lower()])


def _num(block, cls):
    m = re.search(r"class='%s small_post_control'[^>]*>([^<]*)<" % cls, block)
    return parse_count(m.group(1)) if m else 0


def parse_page(page, now):
    posts = []
    for block in page.split("<div class='post_el_small'>")[1:]:
        m = re.search(r"href='/post/(\w+)\.html'", block)
        if not m:
            continue
        pid = m.group(1)
        pt = re.search(r"<div class='post_text[^>]*>(.*?)</div>", block, re.S)
        inner = pt.group(1) if pt else ""
        performers = [H.unescape(x).strip() for x in re.findall(r"data-subkey='([^']*)'", inner)]
        tags = [H.unescape(x).strip().lower() for x in re.findall(r'class="hash_link[^"]*"\s+label="([^"]*)"', inner)]
        head = re.split(r'<a class="hash_link', inner)[0]
        head = re.sub(r"<span class='sub_el.*?</span>", "", head, flags=re.S)
        title = re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", "", head))).strip() or pid
        th = re.search(r"data-src='([^']*)'", block)
        thumb = th.group(1) if th else ""
        if thumb.startswith("//"):
            thumb = "https:" + thumb
        tm = re.search(r"post_control_time'><span>([^<]*)</span><strong>.*?</strong>\s*([\d.,]+\s*[kKmM]?)\s*views", block, re.S)
        rel, views = (tm.group(1), parse_count(tm.group(2))) if tm else ("", 0)
        posts.append(dict(
            id=pid, title=title, performers=performers, tags=tags, thumb=thumb,
            uploaded_at=parse_rel(rel, now), rel=rel, views=views,
            likes=_num(block, "vid_like_blog"), orgasms=_num(block, "vid_orgasm_blog"),
            playlists=_num(block, "vid_playlist_blog"), comments=_num(block, "comments_blog")))
    return posts


def crawl(cfg, full):
    """Returns (posts, pages). Raises on first-page failure."""
    posts, seen, pages = [], set(), 0
    limit = cfg["max_pages"] if full else 1
    for i in range(limit):
        url = "%s/blog/@%s/%d.html" % (cfg["site"].rstrip("/"), cfg["username"], i * 20)
        try:
            page = fetch(url)
        except Exception:
            if i == 0:
                raise
            raise RuntimeError("page %d failed" % (i + 1))
        got = [p for p in parse_page(page, int(time.time())) if p["id"] not in seen]
        if not got:
            break
        pages += 1
        for p in got:
            seen.add(p["id"])
            posts.append(p)
        if "rel='next'" not in page and 'rel="next"' not in page:
            break
        if i + 1 < limit:
            time.sleep(cfg["request_delay_sec"])
    return posts, pages


def save(c, posts, now, complete):
    today = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    existing = {r["id"]: r for r in c.execute("SELECT * FROM posts")}
    first_run = not existing
    new = 0
    for p in posts:
        old = existing.get(p["id"])
        m = (p["views"], p["likes"], p["orgasms"], p["playlists"], p["comments"])
        url = "%s/post/%s.html" % ("https://sxyprn.com", p["id"])
        if old is None:
            new += 1
            c.execute("INSERT INTO posts(id,title,url,thumb,uploaded_at,first_seen,last_seen,last_snap,views,likes,orgasms,playlists,comments,active) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                      (p["id"], p["title"], url, p["thumb"], p["uploaded_at"], now, now, now) + m)
            c.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?,?)", (p["id"], now) + m)
        else:
            changed = m != (old["views"], old["likes"], old["orgasms"], old["playlists"], old["comments"])
            snap = changed or now - (old["last_snap"] or 0) >= 3300
            c.execute("UPDATE posts SET title=?,thumb=?,last_seen=?,last_snap=?,views=?,likes=?,orgasms=?,playlists=?,comments=?,active=1 WHERE id=?",
                      (p["title"], p["thumb"] or old["thumb"], now, now if snap else old["last_snap"]) + m + (p["id"],))
            if snap:
                c.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?,?)", (p["id"], now) + m)
        c.execute("DELETE FROM post_tags WHERE post_id=?", (p["id"],))
        for t in set(p["tags"]):
            c.execute("INSERT OR IGNORE INTO post_tags VALUES(?,?,?)", (p["id"], "tag", t))
        for t in set(p["performers"]):
            c.execute("INSERT OR IGNORE INTO post_tags VALUES(?,?,?)", (p["id"], "performer", t))
        c.execute("INSERT INTO daily_stats VALUES(?,?,?,?,?,?,?) ON CONFLICT(post_id,day) DO UPDATE SET views=excluded.views,likes=excluded.likes,orgasms=excluded.orgasms,playlists=excluded.playlists,comments=excluded.comments",
                  (p["id"], today) + m)
    if complete:  # videos that vanished from a complete crawl were removed
        ids = [p["id"] for p in posts]
        c.execute("UPDATE posts SET active=0 WHERE id NOT IN (%s)" % ",".join("?" * len(ids)), ids) if ids else None
    return new, first_run


def run(cfg, full):
    now = int(time.time())
    c = db.conn()
    try:
        try:
            posts, pages = crawl(cfg, full)
            complete = full
            new, _ = save(c, posts, now, complete)
            c.execute("INSERT INTO scrapes(ts,kind,ok,pages,posts,new_posts,error) VALUES(?,?,1,?,?,?,NULL)",
                      (now, "full" if full else "quick", pages, len(posts), new))
            db.kv_set(c, "fail_streak", 0)
            db.kv_set(c, "last_ok", now)
            if full:
                db.kv_set(c, "last_full", now)
            c.commit()
            return dict(ok=True, pages=pages, posts=len(posts), new=new)
        except Exception as e:
            c.rollback()
            streak = int(db.kv_get(c, "fail_streak", 0)) + 1
            db.kv_set(c, "fail_streak", streak)
            c.execute("INSERT INTO scrapes(ts,kind,ok,pages,posts,new_posts,error) VALUES(?,?,0,0,0,0,?)",
                      (now, "full" if full else "quick", str(e)[:300]))
            c.commit()
            return dict(ok=False, error=str(e))
    finally:
        c.close()
