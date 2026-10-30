"""Upload the newest out/short_*.mp4 to YouTube (resumable upload), with safety rails.

Quota: videos.insert costs 1,600 units. Default daily quota is 10,000 and the hourly
stats job uses ~72, so the hard cap here is 5 uploads/day.
Until the API project passes YouTube's audit, YouTube forces uploads to PRIVATE.
Stdlib only.
"""
import json, os, sys, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(os.environ.get("DATA_DIR", "data"))
OUT = Path(os.environ.get("OUT_DIR", "out"))
DRY = os.environ.get("DRY_RUN", "true").lower() != "false"      # default: do NOT upload
PRIVACY = os.environ.get("PRIVACY", "private")                  # private | unlisted | public
MAX_DAY = min(int(os.environ.get("UPLOAD_MAX_PER_DAY", "2")), 5)
SYNTH = os.environ.get("SYNTHETIC_FLAG", "true").lower() == "true"


def http(method, url, headers=None, data=None):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} {e.read().decode('utf-8', 'replace')[:600]}")


def access_token():
    form = urllib.parse.urlencode({
        "client_id": os.environ["YT_CLIENT_ID"],
        "client_secret": os.environ["YT_CLIENT_SECRET"],
        "refresh_token": os.environ["YT_REFRESH_TOKEN"],
        "grant_type": "refresh_token",
    }).encode()
    _, _, body = http("POST", "https://oauth2.googleapis.com/token",
                      {"Content-Type": "application/x-www-form-urlencoded"}, form)
    return json.loads(body)["access_token"]


def upload(mp4, meta, token):
    snippet = {"title": meta["title"][:100], "description": meta["description"][:4900],
               "tags": ["buildinpublic", "youtube automation", "shorts"], "categoryId": "28"}
    status = {"privacyStatus": PRIVACY, "selfDeclaredMadeForKids": False,
              "containsSyntheticMedia": SYNTH}
    size = mp4.stat().st_size
    _, h, _ = http("POST",
                   "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                   {"Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Upload-Content-Length": str(size),
                    "X-Upload-Content-Type": "video/mp4"},
                   json.dumps({"snippet": snippet, "status": status}).encode())
    _, _, body = http("PUT", h["location"], {"Content-Type": "video/mp4", "Content-Length": str(size)},
                      mp4.read_bytes())
    return json.loads(body)["id"]


def main():
    mode = json.loads((DATA / "mode.json").read_text())
    if mode.get("mode") == "paused":
        print("autopilot paused, no upload")
        return 0
    cap = 1 if mode.get("mode") == "cautious" else MAX_DAY

    files = sorted(OUT.glob("short_*.mp4"))
    if not files:
        print("no video found in", OUT)
        return 0
    mp4 = files[-1]
    meta = json.loads(mp4.with_suffix(".json").read_text())
    for k in ("title", "description"):
        assert meta.get(k), f"meta missing {k}"
    assert "kodavm.com" in meta["description"], "description must point to the site"

    log_p = DATA / "upload_log.json"
    log = json.loads(log_p.read_text()) if log_p.exists() else []
    today = datetime.now(timezone.utc).date().isoformat()
    if any(e["file"] == mp4.name for e in log if not e.get("dry")):
        print("already uploaded:", mp4.name)
        return 0
    n_today = sum(1 for e in log if e["ts"][:10] == today and not e.get("dry"))
    if n_today >= cap:
        print(f"daily cap reached ({n_today}/{cap}), mode {mode.get('mode')}")
        return 0

    print(f"{'DRY RUN' if DRY else 'UPLOAD'} | {mp4.name} | {PRIVACY} | synthetic={SYNTH} | {meta['title']}")
    vid = None
    if not DRY:
        vid = upload(mp4, meta, access_token())
        print("uploaded:", f"https://youtube.com/shorts/{vid}")
    log.append({"ts": datetime.now(timezone.utc).isoformat(), "file": mp4.name, "video_id": vid,
                "title": meta["title"], "privacy": PRIVACY, "dry": DRY})
    DATA.mkdir(exist_ok=True)
    log_p.write_text(json.dumps(log[-200:], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
