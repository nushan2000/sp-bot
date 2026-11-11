"""All analytics / report computations."""
import time
from collections import defaultdict
from datetime import date, datetime, timedelta

F = ("views", "likes", "orgasms", "playlists", "comments")


def day_ts(d):
    return int(datetime(d.year, d.month, d.day).timestamp())


def days_between(a, b):
    return [a + timedelta(n) for n in range((b - a).days + 1)]


def resolve_period(p, today=None):
    t = today or date.today()
    if p == "today": return t, t
    if p == "yesterday": return t - timedelta(1), t - timedelta(1)
    if p == "7d": return t - timedelta(6), t
    if p == "30d": return t - timedelta(29), t
    if p == "90d": return t - timedelta(89), t
    if p == "week": return t - timedelta(t.weekday()), t
    if p == "month": return t.replace(day=1), t
    if p == "lastmonth":
        e = t.replace(day=1) - timedelta(1)
        return e.replace(day=1), e
    if p == "year": return t.replace(month=1, day=1), t
    return None


def deltas(c, d_from, d_to):
    """{(post_id, day): {field: gain}} computed from end-of-day snapshots."""
    prev = {r["post_id"]: r for r in c.execute(
        "SELECT d.* FROM daily_stats d JOIN (SELECT post_id, MAX(day) md FROM daily_stats WHERE day < ? GROUP BY post_id) m "
        "ON d.post_id=m.post_id AND d.day=m.md", (str(d_from),))}
    upl = {r["id"]: datetime.fromtimestamp(r["uploaded_at"]).strftime("%Y-%m-%d") for r in c.execute("SELECT id, uploaded_at FROM posts")}
    out, last = {}, dict(prev)
    for r in c.execute("SELECT * FROM daily_stats WHERE day BETWEEN ? AND ? ORDER BY post_id, day", (str(d_from), str(d_to))):
        p = last.get(r["post_id"])
        if p is None:
            # first time we see it: if uploaded that very day, everything is new; else unknown history => 0
            base = {f: 0 for f in F} if upl.get(r["post_id"]) == r["day"] else {f: r[f] for f in F}
        else:
            base = p
        out[(r["post_id"], r["day"])] = {f: max(0, r[f] - base[f]) for f in F}
        last[r["post_id"]] = r
    return out


def _sum(rows):
    s = {f: 0 for f in F}
    for r in rows:
        for f in F:
            s[f] += r[f]
    return s


def _summary(c, d_from, d_to, ds):
    tot = _sum(v for (pid, day), v in ds.items())
    up = c.execute("SELECT COUNT(*) n FROM posts WHERE uploaded_at>=? AND uploaded_at<?",
                   (day_ts(d_from), day_ts(d_to + timedelta(1)))).fetchone()["n"]
    n = (d_to - d_from).days + 1
    tot.update(uploads=up, days=n,
               avg_views_per_day=round(tot["views"] / n, 1),
               engagement=round(100 * (tot["likes"] + tot["orgasms"]) / tot["views"], 2) if tot["views"] else 0)
    return tot


def report(c, d_from, d_to):
    ds = deltas(c, d_from, d_to)
    summary = _summary(c, d_from, d_to, ds)
    n = summary["days"]
    pf, pt = d_from - timedelta(n), d_from - timedelta(1)
    prev = _summary(c, pf, pt, deltas(c, pf, pt))
    posts = {r["id"]: dict(r) for r in c.execute("SELECT * FROM posts")}
    series = {d.isoformat(): dict(day=d.isoformat(), uploads=0, views=0, likes=0, orgasms=0, comments=0) for d in days_between(d_from, d_to)}
    per_post, tag_g, perf_g = defaultdict(lambda: {f: 0 for f in F}), defaultdict(lambda: dict(views=0, likes=0, uploads=0)), defaultdict(lambda: dict(views=0, likes=0, uploads=0))
    ptags = defaultdict(list)
    for r in c.execute("SELECT * FROM post_tags"):
        ptags[r["post_id"]].append((r["kind"], r["name"]))
    for (pid, day), v in ds.items():
        s = series[day]
        for f in ("views", "likes", "orgasms", "comments"):
            s[f] += v[f]
        for f in F:
            per_post[pid][f] += v[f]
        for kind, name in ptags.get(pid, []):
            g = (tag_g if kind == "tag" else perf_g)[name]
            g["views"] += v["views"]; g["likes"] += v["likes"]
    t0, t1 = day_ts(d_from), day_ts(d_to + timedelta(1))
    new_posts = []
    for p in posts.values():
        if t0 <= p["uploaded_at"] < t1:
            series[datetime.fromtimestamp(p["uploaded_at"]).strftime("%Y-%m-%d")]["uploads"] += 1
            new_posts.append(p)
            for kind, name in ptags.get(p["id"], []):
                (tag_g if kind == "tag" else perf_g)[name]["uploads"] += 1

    def vid(pid, gain):
        p = posts.get(pid)
        return dict(id=pid, title=p["title"] if p else pid, thumb=p["thumb"] if p else "", url=p["url"] if p else "",
                    views_gain=gain["views"], likes_gain=gain["likes"], total_views=p["views"] if p else 0, uploaded_at=p["uploaded_at"] if p else 0)

    top = sorted(per_post.items(), key=lambda kv: -kv[1]["views"])[:15]
    top_new = sorted(new_posts, key=lambda p: -p["views"])[:10]
    rank = lambda d: sorted(([k, v["views"], v["likes"], v["uploads"]] for k, v in d.items() if v["views"] or v["uploads"]), key=lambda x: (-x[1], -x[3]))[:15]
    days = list(series.values())
    best = max(days, key=lambda s: s["views"]) if days else None
    hours = defaultdict(int)
    for p in new_posts:
        hours[datetime.fromtimestamp(p["uploaded_at"]).hour] += 1
    return dict(
        range=[d_from.isoformat(), d_to.isoformat()], summary=summary, previous=prev, series=days,
        top_videos=[vid(pid, g) for pid, g in top if g["views"] > 0],
        top_new_uploads=[vid(p["id"], dict(views=0, likes=0)) | dict(views_gain=p["views"]) for p in top_new],
        top_tags=[dict(name=a, views=b, likes=l, uploads=u) for a, b, l, u in rank(tag_g)],
        top_performers=[dict(name=a, views=b, likes=l, uploads=u) for a, b, l, u in rank(perf_g)],
        best_day=best, upload_hours=[hours[h] for h in range(24)],
        active_days=sum(1 for s in days if s["uploads"]))


def gain_since(c, ts):
    """{post_id: (views_gain, likes_gain)} since ts using snapshots."""
    base = {r["post_id"]: r for r in c.execute(
        "SELECT s.* FROM snapshots s JOIN (SELECT post_id, MAX(ts) mt FROM snapshots WHERE ts<=? GROUP BY post_id) m ON s.post_id=m.post_id AND s.ts=m.mt", (ts,))}
    first = {r["post_id"]: r for r in c.execute(
        "SELECT s.* FROM snapshots s JOIN (SELECT post_id, MIN(ts) mt FROM snapshots GROUP BY post_id) m ON s.post_id=m.post_id AND s.ts=m.mt")}
    out = {}
    for r in c.execute("SELECT id, views, likes FROM posts WHERE active=1"):
        b = base.get(r["id"]) or first.get(r["id"])
        out[r["id"]] = (max(0, r["views"] - b["views"]), max(0, r["likes"] - b["likes"])) if b else (0, 0)
    return out


def last_upload(c):
    r = c.execute("SELECT MAX(uploaded_at) m FROM posts WHERE active=1").fetchone()
    return r["m"]


def overview(c):
    now = int(time.time())
    t = c.execute("SELECT COUNT(*) videos, COALESCE(SUM(views),0) views, COALESCE(SUM(likes),0) likes, COALESCE(SUM(orgasms),0) orgasms, "
                  "COALESCE(SUM(playlists),0) playlists, COALESCE(SUM(comments),0) comments FROM posts WHERE active=1").fetchone()
    totals = dict(t)
    totals["avg_views"] = round(totals["views"] / totals["videos"], 1) if totals["videos"] else 0
    totals["engagement"] = round(100 * (totals["likes"] + totals["orgasms"]) / totals["views"], 2) if totals["views"] else 0
    today = date.today()
    rt = report(c, today - timedelta(29), today)
    ser = rt["series"]
    td, yd = ser[-1], ser[-2]
    g24 = gain_since(c, now - 86400)
    posts = {r["id"]: dict(r) for r in c.execute("SELECT id,title,thumb,url,views,likes,uploaded_at FROM posts WHERE active=1")}
    trending = sorted(((pid, g[0]) for pid, g in g24.items() if g[0] > 0), key=lambda x: -x[1])[:8]
    top = sorted(posts.values(), key=lambda p: -p["views"])[:8]
    lu = last_upload(c)
    uploads_24h = c.execute("SELECT COUNT(*) n FROM posts WHERE uploaded_at>=? AND active=1", (now - 86400,)).fetchone()["n"]
    return dict(
        totals=totals, last_upload=lu, uploads_24h=uploads_24h, views_24h=sum(g[0] for g in g24.values()),
        likes_24h=sum(g[1] for g in g24.values()),
        today=dict(uploads=td["uploads"], views=td["views"], likes=td["likes"]),
        yesterday=dict(uploads=yd["uploads"], views=yd["views"], likes=yd["likes"]),
        series=ser, top_videos=top,
        trending=[dict(posts[pid], gain=g) for pid, g in trending if pid in posts])


def videos(c, q="", sort="views", tag="", performer="", limit=500):
    now = int(time.time())
    g24 = gain_since(c, now - 86400)
    sql, args = "SELECT * FROM posts WHERE active=1", []
    if q:
        sql += " AND title LIKE ?"; args.append("%" + q + "%")
    if tag:
        sql += " AND id IN (SELECT post_id FROM post_tags WHERE kind='tag' AND name=?)"; args.append(tag)
    if performer:
        sql += " AND id IN (SELECT post_id FROM post_tags WHERE kind='performer' AND name=?)"; args.append(performer)
    rows = []
    for r in c.execute(sql, args):
        d = dict(r)
        d["gain24"] = g24.get(r["id"], (0, 0))[0]
        d["engagement"] = round(100 * (r["likes"] + r["orgasms"]) / r["views"], 2) if r["views"] else 0
        hrs = max(1, (now - r["uploaded_at"]) / 3600)
        d["views_per_day"] = round(r["views"] / max(hrs / 24, 1 / 24), 1)
        rows.append(d)
    key = {"views": "views", "likes": "likes", "new": "uploaded_at", "gain24": "gain24", "engagement": "engagement",
           "comments": "comments", "orgasms": "orgasms", "vpd": "views_per_day"}.get(sort, "views")
    rows.sort(key=lambda d: -d[key])
    return rows[:limit]


def video_detail(c, pid):
    p = c.execute("SELECT * FROM posts WHERE id=?", (pid,)).fetchone()
    if not p:
        return None
    tags = [r["name"] for r in c.execute("SELECT name FROM post_tags WHERE post_id=? AND kind='tag'", (pid,))]
    perf = [r["name"] for r in c.execute("SELECT name FROM post_tags WHERE post_id=? AND kind='performer'", (pid,))]
    daily = [dict(r) for r in c.execute("SELECT day, views, likes, orgasms, comments FROM daily_stats WHERE post_id=? ORDER BY day", (pid,))]
    for i, d in enumerate(daily):
        d["gain"] = d["views"] - (daily[i - 1]["views"] if i else 0 if p["uploaded_at"] and datetime.fromtimestamp(p["uploaded_at"]).strftime("%Y-%m-%d") == d["day"] else d["views"])
    hourly = [dict(r) for r in c.execute("SELECT ts, views, likes FROM snapshots WHERE post_id=? ORDER BY ts", (pid,))][-200:]
    related = []
    for r in c.execute("SELECT * FROM posts WHERE active=1 AND id<>?", (pid,)):
        ot = {x["name"] for x in c.execute("SELECT name FROM post_tags WHERE post_id=? AND kind='tag'", (r["id"],))}
        op = {x["name"] for x in c.execute("SELECT name FROM post_tags WHERE post_id=? AND kind='performer'", (r["id"],))}
        ts, sp = set(tags), set(perf)
        score = (len(ts & ot) / len(ts | ot) if ts | ot else 0) + (0.5 if sp & op else 0)
        if score > 0.2:
            related.append((score, dict(id=r["id"], title=r["title"], views=r["views"], likes=r["likes"])))
    related.sort(key=lambda x: -x[0])
    return dict(video=dict(p), tags=tags, performers=perf, daily=daily, hourly=hourly, related=[r for _, r in related[:8]])


def _agg(c, kind):
    out = []
    for r in c.execute("SELECT t.name, COUNT(*) videos, SUM(p.views) views, SUM(p.likes) likes, SUM(p.orgasms) orgasms, SUM(p.comments) comments, MAX(p.uploaded_at) last_upload "
                       "FROM post_tags t JOIN posts p ON p.id=t.post_id WHERE t.kind=? AND p.active=1 GROUP BY t.name", (kind,)):
        d = dict(r)
        d["avg_views"] = round(d["views"] / d["videos"], 1)
        d["engagement"] = round(100 * (d["likes"] + d["orgasms"]) / d["views"], 2) if d["views"] else 0
        out.append(d)
    out.sort(key=lambda d: -d["views"])
    return out


def insights(c):
    tags, perfs = _agg(c, "tag"), _agg(c, "performer")
    pairs = [dict(r) for r in c.execute(
        "SELECT a.name a, b.name b, COUNT(*) videos, SUM(p.views) views FROM post_tags a JOIN post_tags b ON a.post_id=b.post_id AND a.name<b.name "
        "JOIN posts p ON p.id=a.post_id WHERE a.kind='tag' AND b.kind='tag' AND p.active=1 GROUP BY a.name,b.name HAVING videos>=2 ORDER BY videos DESC, views DESC LIMIT 25")]
    perf_tag = []
    for pf in perfs[:8]:
        rows = c.execute("SELECT t2.name, COUNT(*) n FROM post_tags t1 JOIN post_tags t2 ON t1.post_id=t2.post_id AND t2.kind='tag' WHERE t1.kind='performer' AND t1.name=? GROUP BY t2.name ORDER BY n DESC LIMIT 5", (pf["name"],)).fetchall()
        perf_tag.append(dict(performer=pf["name"], tags=[r["name"] for r in rows]))
    hours = [dict(hour=h, uploads=0, views=0) for h in range(24)]
    wd = [dict(day=n, uploads=0, views=0) for n in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")]
    for r in c.execute("SELECT uploaded_at, views FROM posts WHERE active=1"):
        dt = datetime.fromtimestamp(r["uploaded_at"])
        for b in (hours[dt.hour], wd[dt.weekday()]):
            b["uploads"] += 1; b["views"] += r["views"]
    for b in hours + wd:
        b["avg_views"] = round(b["views"] / b["uploads"], 1) if b["uploads"] else 0
    best = max((h for h in hours if h["uploads"] >= 2), key=lambda h: h["avg_views"], default=None)
    return dict(tags=tags, performers=perfs, pairs=pairs, performer_tags=perf_tag, hours=hours, weekdays=wd, best_hour=best)


def monthly(c):
    """Month-by-month rollup for the whole history."""
    first = c.execute("SELECT MIN(day) d FROM daily_stats").fetchone()["d"]
    if not first:
        return []
    d0 = datetime.strptime(first, "%Y-%m-%d").date().replace(day=1)
    today, out = date.today(), []
    while d0 <= today:
        nxt = (d0.replace(day=28) + timedelta(4)).replace(day=1)
        e = min(nxt - timedelta(1), today)
        ds = deltas(c, d0, e)
        s = _summary(c, d0, e, ds)
        out.append(dict(month=d0.strftime("%Y-%m"), uploads=s["uploads"], views=s["views"], likes=s["likes"], comments=s["comments"], engagement=s["engagement"]))
        d0 = nxt
    return out


def sms_summary(c, d):
    r = report(c, d, d)
    s = r["summary"]
    top = r["top_videos"][0] if r["top_videos"] else None
    ov = c.execute("SELECT COALESCE(SUM(views),0) v, COUNT(*) n FROM posts WHERE active=1").fetchone()
    msg = "Daily report %s: %d uploads, +%d views, +%d likes. Total %d videos / %d views." % (d.isoformat(), s["uploads"], s["views"], s["likes"], ov["n"], ov["v"])
    if top:
        msg += " Top: %s (+%d)" % (top["title"][:50], top["views_gain"])
    return msg
