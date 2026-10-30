"""Data -> script -> Piper voice -> 1080x1920 frames -> ffmpeg -> out/short_*.mp4
No AI API needed: template script writer + local Piper TTS + Pillow + ffmpeg.
"""
import json, math, os, random, subprocess, sys, wave
from datetime import date
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont

DATA = Path(os.environ.get("DATA_DIR", "data"))
OUT = Path(os.environ.get("OUT_DIR", "out"))
VOICE = os.environ.get("PIPER_VOICE", "en_US-lessac-medium")
VOICE_DIR = os.environ.get("PIPER_DIR", "voices")
FPS = int(os.environ.get("FPS", "24"))
W, H = 1080, 1920
SITE = "kodavm.com"

# ---------- 1. script writer (template pools, no AI) ----------
HOOKS = {
 "up": ["Day {day}. The channel gained {subs_day} subscribers today.",
        "Day {day} of building in public. That is plus {subs_day} subscribers.",
        "{subs_day} new subscribers since yesterday. Here is what the bot did."],
 "flat": ["Day {day}. Zero growth today. Here is the honest report.",
          "Day {day}. Nothing moved today, and I am showing you anyway.",
          "No new subscribers today. Day {day}, and the numbers stay public."],
 "near": ["Day {day}. Only {left} subscribers away from {next_k}.",
          "{left} to go until {next_k}. Day {day} of the experiment."],
 "hit":  ["Milestone reached. {prev_k} subscribers on day {day}.",
          "Day {day}. We just crossed {prev_k} subscribers."],
}
COUNTER = ["The channel now has {subs} subscribers and {views} total views.",
           "Right now: {subs} subscribers, {views} views, all pulled from the public data.",
           "The count stands at {subs} subscribers. {views} views so far."]
INSIGHT = ["{why}.", "The autopilot is in {mode} mode. {why}.", "Status check. {why}."]
PACE = ["At this pace, {next_k} is about {eta} days away.",
        "{left} subscribers to {next_k}. That is roughly {eta} days at today's speed."]
CTA = ["Follow the build live at {site}. Every number is real.",
       "The full dashboard is live at {site}. Subscribe to watch it grow."]


def pick(pool, rng, state, key):
    last = state.get(key, -1)
    idx = rng.choice([i for i in range(len(pool)) if i != last] or [0])
    state[key] = idx
    return pool[idx]


def k(n):
    return f"{n // 1000}K" if n >= 1000 and n % 1000 == 0 else f"{n:,}"


def write_script(stats, mode):
    state_p = DATA / "script_state.json"
    state = json.loads(state_p.read_text()) if state_p.exists() else {}
    rng = random.Random(f"{date.today()}-{stats['updated'][:13]}")
    ms = stats["milestone"]
    left = max(ms["next"] - stats["subs"], 0)
    span = max(ms["next"] - ms["prev"], 1)
    pct = (stats["subs"] - ms["prev"]) / span
    f = dict(day=stats["day"], subs=f"{stats['subs']:,}", views=f"{stats['views']:,}",
             subs_day=stats["subsDay"], left=f"{left:,}", next_k=k(ms["next"]),
             prev_k=k(ms["prev"]), eta=math.ceil(left / max(stats["subsDay"], 1)),
             mode=mode["mode"], why=mode["why"].rstrip("."), site=SITE)
    if stats["subs"] in (ms["prev"],) and ms["prev"] > 0:
        kind = "hit"
    elif pct >= 0.8:
        kind = "near"
    elif stats["subsDay"] > 0:
        kind = "up"
    else:
        kind = "flat"
    lines = [("hook", pick(HOOKS[kind], rng, state, "hook_" + kind)),
             ("counter", pick(COUNTER, rng, state, "counter")),
             ("insight", pick(INSIGHT, rng, state, "insight")),
             ("pace", pick(PACE, rng, state, "pace")),
             ("cta", pick(CTA, rng, state, "cta"))]
    out = []
    for scene, tpl in lines:
        text = tpl.format(**f)
        assert "{" not in text and "}" not in text, text   # fact/format guard
        out.append({"scene": scene, "text": text})
    DATA.mkdir(exist_ok=True)
    state_p.write_text(json.dumps(state))
    title = f"Day {stats['day']}: {stats['subs']:,} subscribers, built in public #shorts"
    return out, title, kind


# ---------- 2. voice (Piper, local & free) ----------
def tts(text, path):
    try:
        subprocess.run([sys.executable, "-m", "piper", "-m", VOICE, "--data-dir", VOICE_DIR,
                        "-f", str(path)], input=text.encode(), check=True,
                       capture_output=True, timeout=120)
        return True
    except Exception as exc:
        print("piper failed, using silence:", exc, file=sys.stderr)
        return False


def wav_len(p):
    with wave.open(str(p)) as w:
        return w.getnframes() / w.getframerate()


# ---------- 3. frames ----------
def font(size, bold=True, serif=False):
    n = ("DejaVuSerif-Bold" if bold else "DejaVuSerif") if serif else ("DejaVuSans-Bold" if bold else "DejaVuSans")
    for p in (f"/usr/share/fonts/truetype/dejavu/{n}.ttf", f"{n}.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            pass
    return ImageFont.load_default(size)


def background():
    bg = Image.new("RGB", (W, H), (4, 4, 6))
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    d = ImageDraw.Draw(glow)
    d.ellipse((-100, 300, W + 100, 1100), fill=(40, 48, 110))
    d.ellipse((250, 450, W - 250, 950), fill=(20, 90, 75))
    return Image.blend(bg, glow.filter(ImageFilter.GaussianBlur(260)), 0.75)


def wrap(draw, text, fnt, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=fnt) <= width:
            cur = t
        else:
            lines.append(cur)
            cur = w
    return lines + [cur]


def center(d, y, text, fnt, fill, width=W - 140, gap=1.18):
    for ln in wrap(d, text, fnt, width):
        d.text((W / 2, y), ln, font=fnt, fill=fill, anchor="mm")
        y += int(fnt.size * gap)
    return y


def ease(t):
    return 1 - (1 - min(max(t, 0), 1)) ** 4


def frame(bg, scene, text, t, stats, mode):
    im = bg.copy()
    d = ImageDraw.Draw(im)
    gold, mint, mute = (232, 211, 162), (62, 232, 181), (125, 125, 140)
    d.text((W / 2, 150), f"{SITE.upper()}  ·  DAY {stats['day']}", font=font(34), fill=gold, anchor="mm")
    col = {"normal": mint, "cautious": gold, "paused": (255, 93, 108)}[mode["mode"]]
    d.ellipse((W / 2 - 150, 214, W / 2 - 134, 230), fill=col)
    d.text((W / 2 - 118, 222), f"AUTOPILOT · {mode['mode'].upper()}", font=font(26, False), fill=mute, anchor="lm")
    ms = stats["milestone"]
    if scene in ("counter", "pace", "hook"):
        n = int(stats["subs"] * ease(t * 1.6)) if scene == "counter" else stats["subs"]
        d.text((W / 2, 760), f"{n:,}", font=font(250, True, True), fill=(244, 244, 248), anchor="mm")
        d.text((W / 2, 930), "SUBSCRIBERS", font=font(34), fill=mute, anchor="mm")
        p = (stats["subs"] - ms["prev"]) / max(ms["next"] - ms["prev"], 1)
        x0, x1, y = 120, W - 120, 1060
        d.rounded_rectangle((x0, y, x1, y + 22), 11, fill=(32, 32, 42))
        fw = int((x1 - x0) * p * ease(t * 1.4))
        if fw > 22:
            d.rounded_rectangle((x0, y, x0 + fw, y + 22), 11, fill=mint)
            d.ellipse((x0 + fw - 22, y - 8, x0 + fw + 10, y + 30), fill=(255, 255, 255))
        d.text((x0, y + 62), k(ms["prev"]), font=font(30, False), fill=mute, anchor="lm")
        d.text((x1, y + 62), k(ms["next"]), font=font(30, False), fill=mute, anchor="rm")
        d.text((W / 2, y + 62), f"{p * 100:.1f}%", font=font(32), fill=(244, 244, 248), anchor="mm")
        center(d, 1330, text, font(54, True), (225, 225, 235))
    elif scene == "insight":
        d.text((W / 2, 640), "WHY THE BOT DID THIS", font=font(32), fill=gold, anchor="mm")
        center(d, 820, text, font(72, True, True), (244, 244, 248), gap=1.25)
    else:
        center(d, 780, text, font(78, True, True), (244, 244, 248), gap=1.25)
        d.rounded_rectangle((W / 2 - 260, 1250, W / 2 + 260, 1350), 50, fill=(244, 244, 248))
        d.text((W / 2, 1300), "SUBSCRIBE", font=font(42), fill=(7, 7, 10), anchor="mm")
    return im


# ---------- 4. assemble ----------
def main():
    stats = json.loads((DATA / "stats.json").read_text())
    mode = json.loads((DATA / "mode.json").read_text())
    if mode["mode"] == "paused":
        print("autopilot paused, no video")
        return 0
    script, title, kind = write_script(stats, mode)
    OUT.mkdir(exist_ok=True)
    work = OUT / "work"
    (work / "frames").mkdir(parents=True, exist_ok=True)
    bg, durs, wavs, fidx = background(), [], [], 0
    params = None
    for i, s in enumerate(script):
        p = work / f"s{i}.wav"
        ok = tts(s["text"], p)
        if ok:
            with wave.open(str(p)) as w:
                params = params or w.getparams()
            dur = max(wav_len(p) + 0.45, 2.6)
        else:
            dur, p = max(len(s["text"].split()) * 0.4, 2.6), None
        durs.append(dur)
        wavs.append(p)
        n = int(dur * FPS)
        still = None
        for j in range(n):
            t = j / max(n - 1, 1)
            animated = s["scene"] in ("counter", "pace", "hook")
            if animated or still is None:
                still = frame(bg, s["scene"], s["text"], t, stats, mode)
            still.save(work / "frames" / f"{fidx:05d}.png")
            fidx += 1
    total = sum(durs)
    assert total < 59, f"too long for a Short: {total:.1f}s"
    # merged audio (silence where Piper was unavailable)
    rate = params.framerate if params else 22050
    with wave.open(str(work / "audio.wav"), "wb") as out:
        out.setnchannels(params.nchannels if params else 1)
        out.setsampwidth(params.sampwidth if params else 2)
        out.setframerate(rate)
        for p, dur in zip(wavs, durs):
            sw = (params.sampwidth if params else 2) * (params.nchannels if params else 1)
            if p:
                with wave.open(str(p)) as w:
                    data = w.readframes(w.getnframes())
                out.writeframes(data + b"\0" * (int(dur * rate) * sw - len(data)))
            else:
                out.writeframes(b"\0" * int(dur * rate) * sw)
    mp4 = OUT / f"short_{date.today()}.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS),
                    "-i", str(work / "frames" / "%05d.png"), "-i", str(work / "audio.wav"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",
                    "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", str(mp4)], check=True)
    meta = {"title": title, "kind": kind, "seconds": round(total, 1),
            "voice_synthetic": all(wavs), "script": script,
            "description": f"Day {stats['day']} of an automated channel built in public. Live numbers: https://{SITE}\n"
                           "Script, visuals and voice are generated automatically from public channel data.\n#shorts #buildinpublic"}
    (OUT / f"short_{date.today()}.json").write_text(json.dumps(meta, indent=1))
    print("done:", mp4, f"{total:.1f}s", "| piper voice:", meta["voice_synthetic"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
