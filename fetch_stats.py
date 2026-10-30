"""Hourly YouTube snapshot -> data/*.json  (stdlib only, ~3 quota units per run).

Quota per run: channels.list (1) + playlistItems.list (1) + videos.list (1) = 3
3 units x 24 runs = 72 units/day.
"""
import json, os, statistics, sys, urllib.parse, urllib.request
from datetime import datetime, timezone, date
from pathlib import Path

DATA = Path("data")
API = "https://www.googleapis.com/youtube/v3/"
KEY = os.environ["YT_API_KEY"]
CHANNEL = os.environ["CHANNEL_ID"]
LAUNCH = os.environ.get("LAUNCH_DATE") or date.today().isoformat()
MILESTONES = [100, 500, 1000, 5000, 10000, 50000, 100000]
LEARNING_DAYS = 14


def call(endpoint, **params):
    params["key"] = KEY
    url = API + endpoint + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def load(name, default):
    p = DATA / name
    return json.loads(p.read_text()) if p.exists() else default


def save(name, obj):
    DATA.mkdir(exist_ok=True)
    (DATA / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1))


def now():
    return datetime.now(timezone.utc)


def parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def snapshot():
    ch = call("channels", part="statistics,contentDetails", id=CHANNEL)["items"][0]
    st = ch["statistics"]
    uploads = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    items = call("playlistItems", part="contentDetails", playlistId=uploads, maxResults=15)["items"]
    ids = ",".join(i["contentDetails"]["videoId"] for i in items)
    vids = []
    if ids:
        for v in call("videos", part="snippet,statistics", id=ids)["items"]:
            vids.append({
                "id": v["id"],
                "title": v["snippet"]["title"],
                "published": v["snippet"]["publishedAt"],
                "views": int(v["statistics"].get("viewCount", 0)),
                "likes": int(v["statistics"].get("likeCount", 0)),
            })
    return int(st["subscriberCount"]), int(st["viewCount"]), vids


def hourly_series(history):
    """Views gained per hour for the last 24 hours, from stored snapshots."""
    pts = history[-25:]
    d = [max(0, b["views"] - a["views"]) for a, b in zip(pts, pts[1:])]
    return ([0] * (24 - len(d)) + d)[-24:]


def delta_since(history, hours, key, current):
    cutoff = now().timestamp() - hours * 3600
    past = [h for h in history if parse(h["ts"]).timestamp() <= cutoff]
    return current - past[-1][key] if past else 0


def compute_mode(day, history, vids, subs_7d, prev_mode, errors):
    """normal / cautious / paused, using the agreed thresholds."""
    if os.environ.get("PAUSE", "").lower() == "true":
        return "paused", "Manual PAUSE switch is on."
    if errors >= 2:
        return "paused", "API failed 2 runs in a row."
    if day <= LEARNING_DAYS:
        return "normal", f"Learning period: day {day} of {LEARNING_DAYS}. Only errors and policy warnings stop me."

    # proxy for "first 48h": only videos at least 2 days old
    aged = [v for v in vids if (now() - parse(v["published"])).days >= 2]
    recent = [v for v in aged if (now() - parse(v["published"])).days <= 14]
    med = statistics.median([v["views"] for v in recent]) if recent else 0
    last3 = sorted(aged, key=lambda v: v["published"], reverse=True)[:3]
    weak = len(last3) == 3 and med > 0 and all(v["views"] < 0.5 * med for v in last3)
    exps = load("experiments.json", [])
    losses = 0
    for e in exps:                       # newest first
        if e.get("result") == "loss":
            losses += 1
        elif e.get("result") == "win":
            break
    reasons = []
    if weak:
        reasons.append("last 3 videos are under 50% of the 14-day median")
    if subs_7d <= 0:
        reasons.append("no subscriber growth in 7 days")
    if losses >= 3:
        reasons.append("3 losing tests in a row")

    if reasons:
        since = prev_mode.get("cautious_since") or now().isoformat()
        if (now() - parse(since)).days >= 5:
            return "paused", "Cautious for 5 days without recovery: " + "; ".join(reasons)
        return "cautious", "Rethinking: " + "; ".join(reasons)
    return "normal", "Normal mode: 2-4 videos a day, one variable changed per day."


def main():
    history = load("history.json", [])
    prev_mode = load("mode.json", {})
    errors = prev_mode.get("errors", 0)
    day = (date.today() - date.fromisoformat(LAUNCH)).days + 1

    try:
        subs, views, vids = snapshot()
        errors = 0
    except Exception as exc:             # network / quota / key problems
        print("fetch failed:", exc, file=sys.stderr)
        errors += 1
        subs = views = None

    if subs is not None:
        history.append({"ts": now().isoformat(), "subs": subs, "views": views})
        history = history[-24 * 35:]     # keep ~35 days
        save("history.json", history)
        nxt = next((m for m in MILESTONES if m > subs), MILESTONES[-1])
        prv = max([m for m in MILESTONES if m <= subs] or [0])
        hourly = hourly_series(history)
        save("stats.json", {
            "updated": now().isoformat(), "day": day,
            "subs": subs, "subsDay": delta_since(history, 24, "subs", subs),
            "views": views, "viewsDay": delta_since(history, 24, "views", views),
            "viewsHour": hourly[-1], "hourly": hourly,
            "milestone": {"prev": prv, "next": nxt}, "milestones": MILESTONES,
            "videos": sorted(vids, key=lambda v: v["views"], reverse=True)[:5],
        })
        subs_7d = delta_since(history, 24 * 7, "subs", subs)
        mode, why = compute_mode(day, history, vids, subs_7d, prev_mode, errors)
    else:
        mode, why = compute_mode(day, history, [], 1, prev_mode, errors)

    cs = prev_mode.get("cautious_since")
    cs = (cs or now().isoformat()) if mode == "cautious" else (cs if mode == "paused" else None)
    save("mode.json", {"mode": mode, "why": why, "errors": errors,
                       "cautious_since": cs, "updated": now().isoformat()})
    print(f"day {day} | mode {mode} | {why}")


if __name__ == "__main__":
    main()
