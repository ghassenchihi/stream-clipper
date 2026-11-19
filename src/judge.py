"""judge.py - Claude picks the moments, the score is only the fallback.

Learned from the first Kai Cenat GTA 6 runs: loudness/motion/word scores found 2 of the 10 moments
a human-style read of the transcript picked (story beats: panic, verdicts, "13 years"). So for every
part the hunt writes review/<streamer>/part_<start>.json (full transcript + signals + auto candidates),
logs  STATUS REVIEW <file>  and waits up to job["judge_wait"] s (default 240) for Claude to write
part_<start>.picks.json :
  [{"start": "0:10:12", "end": "0:10:44", "hook": "13 YEARS BRO", "title": "Kai Cenat ... 😭", "why": "..."}]
  ([] = nothing good here). No answer in time -> the automatic picks are used, so it never stalls.
job["judge"] = "auto" turns the wait off.

Also: masks slurs in burned-in captions/hooks, and adds YouTube "Most replayed" data when available.
Hooked into clipper.py with 3 lines right after `peaks = ...` in hunt():
    try: import judge; peaks = judge.decide(job, t0, t1, lines, db, mot, peaks)
    except ImportError: pass
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

M = sys.modules.get("__main__")
_CHOSEN = {}   # judge id -> {"s0", "s1", "hook", "title", "why"}
_HEAT = {}     # url -> list of (start, end, value 0..1)

# ---------------------------------------------------------------- captions: never burn slurs in
SLURS = re.compile(r"\b(n+[i1]+gg+(a|er|as|ers|uh|az)?s?|f+a+gg*(ot|y)?s?|retard(ed)?s?)\b", re.I)


def clean(text):
    return SLURS.sub(lambda m: m.group(0)[0] + "*" * (len(m.group(0)) - 1), text or "")


def _hms(s):
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _sec(t):
    if isinstance(t, (int, float)):
        return float(t)
    p = [float(x) for x in str(t).split(":")]
    while len(p) < 3:
        p.insert(0, 0.0)
    return p[0] * 3600 + p[1] * 60 + p[2]


def _log(msg):
    print(msg, flush=True)


def install():
    """Patch clipper's caption writer (slur masking) and window/hook/title helpers (Claude's picks)."""
    global M
    M = sys.modules.get("__main__")
    if not M or getattr(M, "_judge_installed", False):
        return
    M._judge_installed = True
    if hasattr(M, "write_ass"):
        orig_ass = M.write_ass

        def write_ass(words, path, caption_y, hook=None, hook_y=200):
            words = [dict(w, word=clean(w.get("word", ""))) for w in words]
            return orig_ass(words, path, caption_y, clean(hook) if hook else hook, hook_y)
        M.write_ass = write_ass
    if hasattr(M, "snap_window"):
        orig_snap = M.snap_window

        def snap_window(lines, t_rel, offset, *a, **k):
            c = _CHOSEN.get(int(t_rel + offset))
            return (c["s0"], c["s1"]) if c else orig_snap(lines, t_rel, offset, *a, **k)
        M.snap_window = snap_window
    if hasattr(M, "make_hook"):
        orig_hook = M.make_hook

        def make_hook(cats, said):
            c = next((_CHOSEN[int(x[6:])] for x in cats if x.startswith("judge:") and int(x[6:]) in _CHOSEN), None)
            return clean(c["hook"] if c and c.get("hook") else orig_hook([x for x in cats if not x.startswith("judge:")], said))
        M.make_hook = make_hook
    if hasattr(M, "make_title"):
        orig_title = M.make_title

        def make_title(job, streamer, cats, hook):
            c = next((_CHOSEN[int(x[6:])] for x in cats if x.startswith("judge:") and int(x[6:]) in _CHOSEN), None)
            return clean(c["title"] if c and c.get("title") else orig_title(job, streamer, cats, hook))
        M.make_title = make_title


# ------------------------------------------------------- YouTube "Most replayed" (free viewer signal)
def heat(url):
    if url in _HEAT:
        return _HEAT[url]
    _HEAT[url] = []
    if "youtube" in url or "youtu.be" in url:
        try:
            p = subprocess.run([sys.executable, "-m", "yt_dlp", url, "--skip-download", "--print", "%(heatmap)j"],
                               capture_output=True, text=True, timeout=120)
            hm = json.loads(p.stdout.strip().splitlines()[-1] or "null") or []
            _HEAT[url] = [(h["start_time"], h["end_time"], h["value"]) for h in hm]
            if _HEAT[url]:
                _log(f"  most-replayed data: {len(_HEAT[url])} points")
        except Exception as e:
            _log(f"  no most-replayed data ({str(e)[:80]})")
    return _HEAT[url]


def heat_at(url, t):
    for a, b, v in heat(url):
        if a <= t < b:
            return v
    return None



def _collapse(tr):
    """Whisper hallucinates the same short line over music/cutscenes ("What?" x40 at Speed 0:07:41).
    Collapse runs of >= 4 identical short lines into one flagged line so Claude doesn't pick them."""
    out, i = [], 0
    while i < len(tr):
        j = i
        while j + 1 < len(tr) and tr[j + 1][1].strip().lower() == tr[i][1].strip().lower():
            j += 1
        n = j - i + 1
        if n >= 4 and len(tr[i][1].split()) <= 4:
            out.append([tr[i][0], f"[{tr[i][1]!r} x{n} until {tr[j][0]} - LIKELY WHISPER HALLUCINATION over music/cutscene, not real speech]"])
        else:
            out.extend(tr[i:j + 1])
        i = j + 1
    return out


# ------------------------------------------------------------------------- the review step
def decide(job, t0, t1, lines, db, mot, peaks):
    """Returns the peaks to clip for this part: Claude's picks if they arrive in time, else `peaks`."""
    install()
    url = job["url"]
    streamer = job.get("streamer", "stream")
    # fallback ranking: add the most-replayed bonus (YouTube)
    if heat(url):
        peaks = sorted(((sc + 2.0 * (heat_at(url, t) or 0), t, cats, said) for sc, t, cats, said in peaks),
                       key=lambda p: -p[0])
    if job.get("judge", "claude") != "claude":
        return peaks
    rdir = Path("review") / streamer
    rdir.mkdir(parents=True, exist_ok=True)
    tag = _hms(t0).replace(":", "-")
    rev, picks_f = rdir / f"part_{tag}.json", rdir / f"part_{tag}.picks.json"
    loud10 = []
    try:
        import numpy as np
        n = len(db)
        base = float(np.median(db)) if n else 0.0
        for k in range(0, n, 10):
            seg_db, seg_m = db[k:k + 10], mot[k:k + 10]
            loud10.append([_hms(t0 + k), round(float(seg_db.max() - base), 1), round(float(seg_m.mean()), 1)])
    except Exception:
        pass
    data = {
        "part": [_hms(t0), _hms(t1)], "url": url, "streamer": streamer, "game": job.get("game"),
        "how_to_answer": f"write {picks_f.name} next to this file: a JSON list of up to "
                         f"{job.get('per_chunk', 3)} picks {{start, end (H:MM:SS, 20-45 s, whole sentences), hook "
                         f"(2-5 words, caps), title (factual, <90 chars, 1 emoji), why}} or [] if nothing is good",
        "auto_candidates": [{"t": _hms(t), "score": round(sc, 2), "why": ", ".join(c), "said": s}
                            for sc, t, c, s in peaks],
        "most_replayed": [[_hms(a), round(v, 2)] for a, b, v in heat(url) if t0 <= a < t1 and v >= 0.5],
        "loudness_motion_10s": loud10,   # [time, dB above the part's median, motion]
        "transcript": _collapse([[_hms(t0 + l["start"]), clean(l["text"])] for l in lines]),
    }
    rev.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
    _log(f"STATUS REVIEW part {_hms(t0)}-{_hms(t1)} -> {rev}")
    wait = float(job.get("judge_wait", 240))
    end = time.time() + wait
    while time.time() < end and not picks_f.exists() and not Path("STOP").exists():
        time.sleep(3)
    if not picks_f.exists():
        _log(f"  no picks from Claude in {int(wait)} s -> using the automatic picks")
        return peaks
    time.sleep(1)  # let the writer finish
    try:
        picks = json.loads(picks_f.read_text(encoding="utf-8-sig"))
    except Exception as e:
        _log(f"  picks file unreadable ({e}) -> using the automatic picks")
        return peaks
    out = []
    for p in picks:
        s0, s1 = _sec(p["start"]), _sec(p["end"])
        if not (8 <= s1 - s0 <= 75):
            _log(f"  pick {p.get('start')} ignored: length {s1 - s0:.0f} s")
            continue
        mid = int((s0 + s1) / 2)
        _CHOSEN[mid] = {"s0": s0, "s1": s1, "hook": p.get("hook"), "title": p.get("title"), "why": p.get("why"),
                       "layout": p.get("layout"), "facecam": p.get("facecam")}
        said = " ".join(l["text"] for l in lines if s0 - t0 - 2 <= l["start"] <= s1 - t0)[:200]
        out.append((float(p.get("score", 9.0)), mid, ["judge:%d" % mid, "claude:" + (p.get("why") or "")[:60]], said))
    _log(f"  Claude picked {len(out)} moment(s) in this part")
    return out
