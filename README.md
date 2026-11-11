# SP Bot

Scrapes your public blog page (`https://sxyprn.com/blog/@<username>`), stores history in SQLite,
shows a dashboard + reports, and sends an SMS when you have not uploaded for 1 hour.

    python3 app.py            # no pip install needed (standard library only)
    open http://127.0.0.1:8765

## How it works
- Every 5 min: reads the newest page -> detects new uploads (exact upload time) and checks the stall rule.
- Every 60 min: crawls all pages -> refreshes views / likes / reactions / comments, builds daily history.
- Daily & monthly "views gained" come from end-of-day snapshots, so **history starts the day you first run it**.
  Upload times of videos older than ~1 day are approximate (the site only shows "2 days ago").
- Alert: no new upload for `alert_after_minutes` (60) -> SMS, repeated every `repeat_minutes` (120),
  "uploads resumed" SMS afterwards. Optional quiet hours, daily summary SMS, and an SMS if the site is unreachable.

## SMS setup (Settings page, then "Send test SMS")
- `twilio`   : Account SID, Auth Token, From number
- `notifylk` : Notify.lk user id, API key, sender id
- `http`     : any gateway; URL/body templates with `{to}` `{message}` (JSON: `{to_json}` `{message_json}`)
- `console`  : default, only logs (no SMS)
Recipients: comma separated, international format (+94...). Secrets are stored in config.json (chmod 600).

## Notes
- Login required (VBA_AUTH_USER / VBA_AUTH_PASS). SMS recipients are hard-coded in sms.py. See deploy/ for VPS setup.
- Run in background: `nohup python3 app.py > app.log 2>&1 &` (or a systemd service).
- Data: `data/analytics.db`. Settings: `config.json` (or env `VBA_<KEY>`).
# sp-bot
