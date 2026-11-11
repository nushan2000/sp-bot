import os, sqlite3
from config import BASE

PATH = os.path.join(BASE, "data", "analytics.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts(
  id TEXT PRIMARY KEY, title TEXT, url TEXT, thumb TEXT,
  uploaded_at INTEGER, first_seen INTEGER, last_seen INTEGER, last_snap INTEGER DEFAULT 0,
  views INTEGER DEFAULT 0, likes INTEGER DEFAULT 0, orgasms INTEGER DEFAULT 0,
  playlists INTEGER DEFAULT 0, comments INTEGER DEFAULT 0, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS post_tags(post_id TEXT, kind TEXT, name TEXT, PRIMARY KEY(post_id,kind,name));
CREATE INDEX IF NOT EXISTS ix_pt_name ON post_tags(kind,name);
CREATE TABLE IF NOT EXISTS snapshots(post_id TEXT, ts INTEGER, views INTEGER, likes INTEGER, orgasms INTEGER, playlists INTEGER, comments INTEGER);
CREATE INDEX IF NOT EXISTS ix_snap ON snapshots(post_id, ts);
CREATE INDEX IF NOT EXISTS ix_snap_ts ON snapshots(ts);
CREATE TABLE IF NOT EXISTS daily_stats(post_id TEXT, day TEXT, views INTEGER, likes INTEGER, orgasms INTEGER, playlists INTEGER, comments INTEGER, PRIMARY KEY(post_id, day));
CREATE INDEX IF NOT EXISTS ix_daily_day ON daily_stats(day);
CREATE TABLE IF NOT EXISTS scrapes(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, kind TEXT, ok INTEGER, pages INTEGER, posts INTEGER, new_posts INTEGER, error TEXT);
CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, kind TEXT, message TEXT, sms_ok INTEGER, detail TEXT);
CREATE TABLE IF NOT EXISTS comp_bloggers(
  handle TEXT PRIMARY KEY, name TEXT, base TEXT, rank INTEGER, stars INTEGER DEFAULT 0,
  posts INTEGER DEFAULT 0, vids INTEGER DEFAULT 0, lnks INTEGER DEFAULT 0, likes INTEGER DEFAULT 0,
  fg TEXT, bg TEXT, extra INTEGER DEFAULT 0, active INTEGER DEFAULT 1,
  first_seen INTEGER, last_seen INTEGER, last_crawl INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS comp_hist(handle TEXT, ts INTEGER, rank INTEGER, posts INTEGER, vids INTEGER, lnks INTEGER, likes INTEGER);
CREATE INDEX IF NOT EXISTS ix_ch ON comp_hist(handle, ts);
CREATE TABLE IF NOT EXISTS comp_posts(
  id TEXT PRIMARY KEY, handle TEXT, title TEXT, thumb TEXT, uploaded_at INTEGER, exact INTEGER DEFAULT 0,
  first_seen INTEGER, last_seen INTEGER, views INTEGER DEFAULT 0, likes INTEGER DEFAULT 0,
  orgasms INTEGER DEFAULT 0, comments INTEGER DEFAULT 0, tags TEXT DEFAULT '', performers TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS ix_cp ON comp_posts(handle, uploaded_at);
CREATE TABLE IF NOT EXISTS comp_snaps(post_id TEXT, ts INTEGER, views INTEGER, likes INTEGER);
CREATE INDEX IF NOT EXISTS ix_cs ON comp_snaps(post_id, ts);
CREATE TABLE IF NOT EXISTS comp_events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, handle TEXT, kind TEXT, message TEXT);
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
"""


def conn():
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    c = sqlite3.connect(PATH, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init():
    c = conn()
    c.executescript(SCHEMA)
    c.commit()
    c.close()


def kv_get(c, k, default=None):
    r = c.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return r["v"] if r else default


def kv_set(c, k, v):
    c.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))
