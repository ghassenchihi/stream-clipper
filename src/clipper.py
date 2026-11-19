"""
clipper.py - turn stream peaks into 9:16 captioned Shorts.

Job mode (used by the stream-clipper skill):
  python clipper.py --job job.json

job.json:
{
  "url": "https://www.twitch.tv/videos/2932305259",
  "streamer": "kaicenat",
  "sections": [{"start": "1:02:10", "end": "1:02:40", "title": "..."}, ...],
  "height": 720,
  "facecam": [0.0, 0.4, 0.204, 0.204],   # x, y, w, h as fractions of the frame, or null
  "model": "small"
}

Each section is downloaded on its own (only the peaks, never the whole VOD),
transcribed with faster-whisper (word timings), cut to 9:16 with burned-in captions,
and saved to clips/<streamer>_<YYYY-MM-DD>/<streamer>_NN.mp4.
Progress goes to stdout (run_job.bat sends it to run_log.txt).
"""

import argparse
import re
import threading
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

try:  # pip install static-ffmpeg -> ffmpeg without a separate install
    import static_ffmpeg
    static_ffmpeg.add_paths()
except ImportError:
    pass

WORK = Path("clipper_work")


def load_wav(path):
    """16 kHz mono wav -> float32 array (whisper input; avoids PyAV version problems)."""
    import numpy as np
    import wave
    with wave.open(str(path)) as f:
        return np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0


def add_nvidia_dlls():
    """Let ctranslate2 find cuBLAS/cuDNN installed by install_gpu.bat (pip nvidia-* wheels)."""
    import os
    import site
    for sp in site.getsitepackages() + [site.getusersitepackages()]:
        base = Path(sp) / "nvidia"
        for b in base.glob("*/bin"):
            try:
                os.add_dll_directory(str(b))
            except (AttributeError, OSError):
                pass
            os.environ["PATH"] = str(b) + os.pathsep + os.environ.get("PATH", "")


def whisper_model(size):
    """CPU by default (this PC has no working CUDA libs, and the GPU test once hung).
    Set CLIPPER_GPU=1 after install_gpu.bat to try the GPU."""
    import os
    import numpy as np
    add_nvidia_dlls()
    from faster_whisper import WhisperModel
    if os.environ.get("CLIPPER_GPU") != "1":
        log(f"Whisper {size} on CPU")
        return WhisperModel(size, device="cpu", compute_type="int8")
    try:
        m = WhisperModel(size, device="cuda", compute_type="float16")
        list(m.transcribe(np.zeros(16000, dtype=np.float32))[0])  # forces a real GPU run
        log(f"Whisper {size} on GPU")
        return m
    except Exception as e:
        log(f"GPU not usable ({str(e)[:80]}), using CPU")
        return WhisperModel(size, device="cpu", compute_type="int8")


def log(msg):
    print(msg, flush=True)


def run(cmd, **kw):
    log("> " + " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True, **kw)


def to_sec(t):
    if isinstance(t, (int, float)):
        return float(t)
    parts = [float(p) for p in str(t).split(":")]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    h, m, s = parts
    return h * 3600 + m * 60 + s


def hms(s):
    s = int(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


# ------------------------------------------------------------ 01 Download peak
LIVE = {"on": False, "from_start": False}  # set by hunt (live, or just-ended "post_live" YouTube streams)


def live_args(url):
    yt = "youtube" in url or "youtu.be" in url
    return ["--live-from-start"] if yt and (LIVE["on"] or LIVE["from_start"]) else []


def hls_section(url, start, end, height, out):
    """Fast partial download: fetch only the HLS fragments covering [start, end] (8 at a time),
    join them, trim to the exact range. Returns False if the stream isn't plain HLS."""
    import urllib.request
    from concurrent.futures import ThreadPoolExecutor
    from urllib.parse import urljoin
    yt = "youtube" in url or "youtu.be" in url
    m3u8 = None
    # YouTube only exposes the rewindable HLS feed to some player clients
    for extra in ([["--extractor-args", f"youtube:player_client={c}"] for c in ("web_safari", "ios", "web")]
                  if yt else [[]]):
        p = subprocess.run([sys.executable, "-m", "yt_dlp", url, *extra, "-g", "-f",
                            f"b[height<={height}][protocol^=m3u8]/b[protocol^=m3u8]/b[height>={height}][protocol^=m3u8]"],
                           capture_output=True, text=True)
        lines = [l for l in p.stdout.strip().splitlines() if l.startswith("http")]
        if p.returncode == 0 and len(lines) == 1:
            m3u8 = lines[0]
            break
        log(f"  no HLS feed via {extra[-1] if extra else 'default'}: {(p.stderr or '').strip()[-160:]}")
    if not m3u8:
        return False
    text = urllib.request.urlopen(m3u8, timeout=30).read().decode("utf-8", "ignore")
    segs, t, dur = [], 0.0, None
    for line in text.splitlines():
        if line.startswith("#EXTINF:"):
            dur = float(line[8:].split(",")[0])
        elif line and not line.startswith("#") and dur is not None:
            segs.append((t, dur, urljoin(m3u8, line)))
            t += dur
            dur = None
    # a live playlist may only keep the last hours: line it up with the time since the stream began
    shift = 0.0
    if LIVE.get("total") and t < LIVE["total"] - 120:
        shift = LIVE["total"] - t
        log(f"  feed keeps only the last {hms(t)}; it starts at {hms(shift)}")
        segs = [(a + shift, d, u) for a, d, u in segs]
    pick = [sg for sg in segs if sg[0] + sg[1] > start and sg[0] < end]
    if start < shift:
        log(f"  {hms(start)} is older than the rewind window ({hms(shift)})")
    if not pick:
        return False

    def get(sg):
        for _ in range(3):
            try:
                return urllib.request.urlopen(sg[2], timeout=60).read()
            except Exception:
                time.sleep(2)
        return b""

    with ThreadPoolExecutor(8) as ex:
        data = list(ex.map(get, pick))
    joined = Path(str(out) + ".join.ts")
    with open(joined, "wb") as f:
        for d in data:
            f.write(d)
    off = start - pick[0][0]
    run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{max(off, 0):.2f}", "-i", str(joined),
         "-t", f"{end - start:.2f}", "-c", "copy", str(out)])
    joined.unlink(missing_ok=True)
    return Path(out).exists() and Path(out).stat().st_size > 0


def download_section(url, start, end, height, out):
    """Download ONLY [start, end] of the stream at <= height (never the whole stream)."""
    Path(out).unlink(missing_ok=True)
    try:
        if hls_section(url, start, end, height, out):
            return
    except Exception as e:
        log(f"  fast fragment download failed ({e}), using yt-dlp sections")
    if LIVE["on"] and ("youtube" in url or "youtu.be" in url):
        raise RuntimeError("YouTube live: no HLS feed available for this part")
    run([
        sys.executable, "-m", "yt_dlp", url, *live_args(url),
        "-f", f"bv*[height<={height}]+ba/b[height<={height}]/wv*+ba/w",
        "--download-sections", f"*{hms(start)}-{hms(end)}",
        "--merge-output-format", "mp4", "-N", "8",
        "-o", str(out), "--force-overwrites", "--no-progress",
    ])


# --------------------------------------------------------------- 02 Transcribe
_model = None


def transcribe(video, model_size):
    global _model
    from faster_whisper import WhisperModel
    if _model is None:
        _model = whisper_model(model_size)
    wav = video.with_suffix(".wav")
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
    segments, _ = _model.transcribe(load_wav(wav), word_timestamps=True, vad_filter=True)
    words = []
    for seg in segments:
        for w in seg.words or []:
            words.append({"start": round(w.start, 2), "end": round(w.end, 2), "word": w.word.strip()})
    wav.unlink(missing_ok=True)
    return words


# ----------------------------------------------------------- 03 Cut + caption
def ass_time(t):
    t = max(t, 0)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def write_ass(words, path, caption_y, hook=None, hook_y=200):
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Arial Black,88,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,6,2,5,60,60,0,1
Style: Hook,Arial Black,76,&H0000FFFF,&H000000FF,&H00000000,&HC0000000,1,0,0,0,100,100,0,0,3,4,0,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for i in range(0, len(words), 3):  # 3 words at a time, spoken word in yellow
        chunk = words[i:i + 3]
        for j, w in enumerate(chunk):
            w_end = chunk[j + 1]["start"] if j + 1 < len(chunk) else w["end"]
            txt = " ".join(
                ("{\\c&H00FFFF&}" + x["word"].upper() + "{\\c&HFFFFFF&}") if k == j else x["word"].upper()
                for k, x in enumerate(chunk)
            )
            lines.append(f"Dialogue: 0,{ass_time(w['start'])},{ass_time(w_end)},Cap,,0,0,0,,{{\\pos(540,{caption_y})}}{txt}")
    if hook:  # short title on the first ~3 s, boxed, above the captions
        lines.insert(0, f"Dialogue: 1,0:00:00.00,0:00:03.20,Hook,,0,0,0,,{{\\pos(540,{hook_y})\\fad(150,250)}}{hook.upper()}")
    path.write_text(header + "\n".join(lines), encoding="utf-8")


def find_cam_box(img):
    """img: HxWx3 uint8. Find the streamer cam as the biggest purple/pink-lit rectangle in the left
    40% of the frame (Kai Cenat's room light). Returns [x, y, w, h] fractions or None."""
    import numpy as np
    H, W, _ = img.shape
    r, g, b = [img[..., i].astype(int) for i in range(3)]
    mask = (r > 110) & (b > 110) & (g < np.minimum(r, b) - 25)
    mask[:, int(W * 0.4):] = False
    if mask.sum() < H * W * 0.004:
        return None
    rows = np.where(mask.sum(axis=1) > W * 0.02)[0]
    cols = np.where(mask.sum(axis=0) > H * 0.02)[0]
    if len(rows) < 5 or len(cols) < 5:
        return None
    y0, y1, x0, x1 = rows.min(), rows.max(), cols.min(), cols.max()
    w, h = (x1 - x0) / W, (y1 - y0) / H
    if not (0.08 < w < 0.4 and 0.08 < h < 0.6 and 1.0 < (w * W) / (h * H) < 2.6):
        return None
    pad = 0.01
    return [max(0, x0 / W - pad), max(0, y0 / H - pad), min(1, w + 2 * pad), min(1, h + 2 * pad)]


def auto_cam(src):
    """Look at 3 frames of the clip; use the cam box only if they agree."""
    import numpy as np
    dur = media_seconds(src) or 30
    boxes = []
    for f in (0.25, 0.5, 0.75):
        p = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{dur * f:.1f}", "-i", str(src), "-frames:v", "1",
                            "-vf", "scale=320:180", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
        if len(p.stdout) == 320 * 180 * 3:
            boxes.append(find_cam_box(np.frombuffer(p.stdout, np.uint8).reshape(180, 320, 3)))
    good = [b for b in boxes if b]
    if len(good) >= 2 and max(abs(a[1] - c[1]) for a in good for c in good) < 0.05:
        return [round(float(np.median([b[i] for b in good])), 3) for i in range(4)]
    return None


def media_dims(path):
    """(width, height) of the first video stream; (1280, 720) if unknown."""
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        w, h = p.stdout.strip().split(",")[:2]
        return int(w), int(h)
    except ValueError:
        return 1280, 720


def render(src, words, out, facecam, layout=None, center_x=0.5, hook=None):
    """layout: 'facecam' (cam box on top, gameplay below), 'center' (9:16 crop around center_x),
    'fit' (whole 16:9 frame in the middle on a blurred copy - safe when the screen keeps changing)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    ass = out.with_suffix(".ass")
    layout = layout or ("facecam" if facecam else "center")
    if facecam == "auto" or layout == "auto":
        box = auto_cam(src)
        if box:
            facecam, layout = box, "facecam"
            log(f"  cam found at {box}")
        else:
            layout = "fit"
            log("  cam not found for sure -> fit layout")
    if layout == "facecam" and facecam:
        x, y, w, h = facecam
        # Natural cam (learned on Speed GTA 6): keep the cam's own shape (the old fixed 1080x608 cut
        # off his head and chin), trim 2-4% inside the box (overlay borders / light lines), clean
        # compression noise, upscale with lanczos, adaptive sharpen, a touch of colour, thin divider.
        sw, sh = media_dims(src)
        x2, y2, w2, h2 = x + w * 0.02, y + h * 0.02, w * 0.96, h * 0.94
        cam_h = int(round(1080 * (h2 * sh) / max(1, w2 * sw) / 2)) * 2
        cam_h = min(max(cam_h, 540), 780)
        game_h = 1920 - cam_h
        vf = (f"[0:v]crop=iw*{w2:.4f}:ih*{h2:.4f}:iw*{x2:.4f}:ih*{y2:.4f},hqdn3d=1.5:1.5:4:4,"
              f"scale=1080:{cam_h}:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:{cam_h},"
              f"cas=0.6,eq=saturation=1.06:contrast=1.03,"
              f"drawbox=x=0:y=ih-5:w=iw:h=5:color=black@0.85:t=fill,setsar=1[cam];"
              f"[0:v]crop=ih*1080/{game_h}:ih,scale=1080:{game_h}:flags=lanczos,cas=0.3,setsar=1[game];"
              f"[cam][game]vstack,ass={ass.name}[v]")
        write_ass(words, ass, cam_h, hook, cam_h + 150)
    elif layout == "fit":
        vf = ("[0:v]split[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
              "boxblur=20:2,eq=brightness=-0.06[bg];[b]hqdn3d=1.5:1.5:4:4,scale=1080:-2:flags=lanczos,cas=0.4[fg];"
              f"[bg][fg]overlay=0:(H-h)/2,setsar=1,ass={ass.name}[v]")
        write_ass(words, ass, 1290, hook, 330)
    else:  # center crop around center_x
        cx = min(max(float(center_x), 0.2813), 0.7187)  # keep the 9:16 window inside the frame
        vf = (f"[0:v]crop=ih*9/16:ih:iw*{cx}-ih*9/32:0,hqdn3d=1.5:1.5:4:4,scale=1080:1920:flags=lanczos,"
              f"cas=0.5,setsar=1,ass={ass.name}[v]")
        write_ass(words, ass, 1250, hook, 300)
    run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(src.resolve()),
        "-filter_complex", vf, "-map", "[v]", "-map", "0:a?", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out.name,
    ], cwd=out.parent)  # cwd avoids Windows path escaping in the ass filter
    ass.unlink(missing_ok=True)


# ------------------------------------------------- 00 Scan (watch at ~160p)
GAME_CUES = {  # "plugin" per game: event categories and the words that signal them
    "gta": {
        "chase": ["cops", "police", "chasing", "chase", "wanted", "stars", "lose them", "run", "drive", "go go"],
        "crash": ["crash", "crashed", "hit", "flipped", "wrecked", "car", "boom", "explode", "blew up"],
        "stunt": ["jump", "flip", "stunt", "fly", "flying", "landed", "ramp", "parachute"],
        "combat": ["shoot", "shot", "gun", "kill", "killed", "fight", "punch", "dead", "died", "headshot"],
        "npc_chaos": ["npc", "random", "this guy", "who is this", "lady", "dude just"],
        "mission": ["mission", "heist", "bank", "passed", "failed", "money", "robbed", "escaped"],
    },
}
NOT_NOW = ["i remember", "last time", "back when", "imagine if", "what if", "if i", "i would", "i'm gonna"]
GAME_WORDS = {g: sorted({w for ws in c.values() for w in ws}) for g, c in GAME_CUES.items()}
CHAT_HYPE = ["kekw", "lul", "omegalul", "lmao", "lmfao", "pog", "poggers", "w", "l", "💀", "😭", "🔥",
             "clip", "clip it", "no way", "wtf", "bro", "?", "!!", "hahaha", "aware", "icant", "sheesh"]
HYPE = ["no way", "what", "wait", "oh my god", "let's go", "lets go", "bro", "yo", "chat",
        "insane", "crazy", "holy", "what the", "are you serious", "nah", "clip"]


def chat_per_second(url, n, out):
    """Twitch chat replay -> messages/sec + hype-emote hits/sec (needs pip chat-downloader)."""
    import numpy as np
    rate, hype = np.zeros(n), np.zeros(n)
    try:
        from chat_downloader import ChatDownloader
        log("== 00 Scan: reading chat replay ==")
        k = 0
        for m in ChatDownloader().get_chat(url):
            t = int(m.get("time_in_seconds", -1))
            if 0 <= t < n:
                rate[t] += 1
                msg = (m.get("message") or "").lower()
                if any(h == msg.strip() or h in msg.split() for h in CHAT_HYPE):
                    hype[t] += 1
            k += 1
            if k % 20000 == 0:
                log(f"  chat: {k} messages")
        log(f"  chat: {k} messages total")
    except Exception as e:
        log(f"  chat replay unavailable ({e}); continuing without it")
    (out / "chat.json").write_text(json.dumps({"rate": rate.tolist(), "hype": hype.tolist()}), encoding="utf-8")
    return rate, hype


def motion_per_second(video, n):
    """Average frame difference per second on a tiny 64x36 grayscale copy (2 fps)."""
    import numpy as np
    p = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(video), "-vf", "fps=2,scale=64:36,format=gray",
                        "-f", "rawvideo", "-"], capture_output=True, check=True)
    fr = np.frombuffer(p.stdout, dtype=np.uint8).reshape(-1, 36 * 64).astype(np.float32)
    d = np.abs(np.diff(fr, axis=0)).mean(axis=1)
    mot = np.zeros(n)
    for i, v in enumerate(d):
        if i // 2 < n:
            mot[i // 2] = max(mot[i // 2], v)
    return mot


def z(a):
    import numpy as np
    return (a - a.mean()) / (a.std() + 1e-9)


def scan(url, model_size, game=None):
    """Watch the whole VOD cheaply: lowest video quality, full transcript, loudness,
    contact sheets. Writes everything to scan/ for Claude to read."""
    import numpy as np
    import wave
    out = Path("scan")
    out.mkdir(exist_ok=True)
    WORK.mkdir(exist_ok=True)
    video = WORK / "scan.mp4"
    if video.exists():
        log("== 00 Scan: low-quality copy already downloaded, reusing it ==")
    else:
        log("== 00 Scan: downloading the whole VOD at the lowest video quality ==")
        run([sys.executable, "-m", "yt_dlp", url,
             "-f", "b[height<=160]/b[height<=360]/worst[vcodec!=none]",
             "-o", str(video), "--no-progress"])
    # loudness per second
    wav = WORK / "scan.wav"
    run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
    with wave.open(str(wav)) as f:
        sr = f.getframerate()
        a = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16).astype(np.float32)
    n = len(a) // sr
    db = 20 * np.log10(np.sqrt((a[: n * sr].reshape(n, sr) ** 2).mean(axis=1)) + 1e-9)
    z = (db - db.mean()) / (db.std() + 1e-9)
    (out / "loudness.json").write_text(json.dumps([round(float(v), 2) for v in z]), encoding="utf-8")
    z_ = globals()["z"]
    # contact sheets: 1 frame every 20 s, 6x5 grid = 10 minutes per sheet
    if not list(out.glob("sheet_*.jpg")):
      log("== 00 Scan: contact sheets ==")
      run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vf",
         "fps=1/20,scale=320:-2,tile=6x5",  # sheet k, cell i (row-major) = (k*30+i)*20 s
         str(out / "sheet_%02d.jpg")])
    # transcript (base model = fast); reuse if it already exists
    tpath = out / "transcript.json"
    if tpath.exists():
        lines = json.loads(tpath.read_text(encoding="utf-8"))
        log("== 00 Scan: reusing transcript ==")
    else:
        log("== 00 Scan: transcribing (this is the slow part) ==")
        m = whisper_model(model_size)
        segs, _ = m.transcribe(a / 32768.0, vad_filter=True)
        lines = []
        for s_ in segs:
            lines.append({"start": round(s_.start, 1), "end": round(s_.end, 1), "text": s_.text.strip()})
            if len(lines) % 50 == 0:
                log(f"  transcribed up to {hms(s_.end)}")
        tpath.write_text(json.dumps(lines, ensure_ascii=False, indent=0), encoding="utf-8")
    log("== 00 Scan: motion ==")
    mot = motion_per_second(video, n)
    rate, chype = chat_per_second(url, n, out)
    # candidate peaks: loudness + chat speed + chat hype + motion + hype/game words (10 s windows)
    w = np.ones(10) / 10
    parts = {"loud": np.convolve(z, w, "same"), "motion": np.convolve(z_(mot), w, "same")}
    if rate.sum() > 0:
        parts["chat"] = np.convolve(z_(rate), w, "same")
        parts["chat_hype"] = np.convolve(z_(chype), w, "same")
    score = parts["loud"] + 0.5 * parts["motion"] + 1.2 * parts.get("chat", 0) + 1.0 * parts.get("chat_hype", 0)
    words = HYPE + GAME_WORDS.get((game or "").lower(), [])
    line_cats = {}  # line index -> [(category, phrase, strength)]
    if (game or "").lower() == "gta":
        try:
            from gta_taxonomy import classify_text  # MIT, from the Clips Kitty GTA plugin
            for i, l in enumerate(lines):
                cues = classify_text(l["text"], roleplay_enabled=True)
                if cues:
                    line_cats[i] = [(c.category, c.phrase, c.strength) for c in cues]
                    t = int(l["start"])
                    best = max(c.strength for c in cues)
                    score[max(0, t - 8):t + 10] += 1.5 * best  # lead-in 8 s, payoff 10 s
            log(f"  GTA plugin rules: {len(line_cats)} lines with event cues")
        except ImportError:
            log("  gta_taxonomy.py missing; using simple word lists")
    for l in lines:
        t = int(l["start"])
        if t < n:
            low = l["text"].lower()
            hits = 0 if any(x in low for x in NOT_NOW) else sum(1 for h in words if h in low)
            score[max(0, t - 3):t + 7] += 0.4 * min(hits, 3)
    picked = []
    for s in np.argsort(score)[::-1]:
        if len(picked) >= 25:
            break
        if all(abs(s - p) > 40 for p in picked):
            picked.append(int(s))
    with open(out / "peaks.txt", "w", encoding="utf-8") as f:
        for p in sorted(picked):
            near = [l["text"] for l in lines if p - 20 <= l["start"] <= p + 20]
            said = " ".join(near)
            low = said.lower()
            cats = sorted({f"{c}:'{ph}'" for i, l in enumerate(lines) if p - 20 <= l["start"] <= p + 20
                           for c, ph, _ in line_cats.get(i, [])})
            if not cats and not line_cats:
                cats = [c for c, ws in GAME_CUES.get((game or "").lower(), {}).items() if any(w in low for w in ws)]
            if any(x in low for x in NOT_NOW):
                cats.append("WARN:story/hypothetical")
            sig = " ".join(f"{k}={v[p]:+.1f}" for k, v in parts.items())
            f.write(f"{hms(p)}  score={score[p]:.2f} [{sig}] cats={', '.join(cats) or '-'}  {said[:300]}\n")
    wav.unlink(missing_ok=True)
    log(f"SCAN DONE: {n // 60} min watched -> {out.resolve()}")


# ---------------------------------------------------------------------- main
def run_job(job_path):
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    streamer = job.get("streamer", "stream")
    day = job.get("day") or dt.date.today().isoformat()
    out_dir = Path("clips") / f"{streamer}_{day}"
    WORK.mkdir(exist_ok=True)
    results = []
    sections = job["sections"]
    for i, sec in enumerate(sections, 1):
        start, end = to_sec(sec["start"]), to_sec(sec["end"])
        log(f"\n== Clip {i}/{len(sections)}: {hms(start)}-{hms(end)} ==")
        try:
            raw = WORK / f"sec{i:02d}.mp4"
            cached = raw_cache(streamer, sec["n"]) if sec.get("n") and not sec.get("fresh") else None
            if cached and cached.exists():
                log(f"  reusing kept 720p source {cached.name} (no download)")
                raw = cached
                words = json.loads(cached.with_suffix(".words.json").read_text(encoding="utf-8"))
            else:
                download_section(job["url"], start, end, job.get("height", 720), raw)
                words = transcribe(raw, job.get("model", "small"))
            out = out_dir / f"{streamer}_{int(sec.get('n', i)):02d}.mp4"  # 'n' = redo one clip by number
            render(raw, words, out, sec.get("facecam", job.get("facecam")),
                   sec.get("layout", job.get("layout")), sec.get("center_x", job.get("center_x", 0.5)),
                   sec.get("hook"))
            said = " ".join(w["word"] for w in words)
            if not cached or not cached.exists():
                keep_raw(raw, words, streamer, int(sec.get("n", i)))
            results.append({"file": str(out), "start": hms(start), "end": hms(end), "title": sec.get("title", ""),
                            "hook": sec.get("hook"),
                            "why": sec.get("why", ""),
                            "transcript": said})
            log(f"OK -> {out}")
        except Exception as e:  # one bad section must not kill the batch
            log(f"FAILED clip {i}: {e}")
            results.append({"file": None, "start": hms(start), "error": str(e)})
    info = out_dir / "clips_info.json"
    if job.get("redo") and info.exists():  # keep the other clips' info when redoing a few
        old = json.loads(info.read_text(encoding="utf-8"))
        done = {r.get("file") for r in results}
        results = [r for r in old if r.get("file") not in done] + results
        results.sort(key=lambda r: r.get("file") or "")
    info.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = sum(1 for r in results if r.get("file"))
    log(f"\nALL DONE: {ok}/{len(sections)} clips in {out_dir.resolve()}")


# ------------------------------------------- Hunt: find a peak, clip it, move on
def stream_info(url):
    """(seconds so far, is_live, was_live) without downloading anything."""
    p = subprocess.run([sys.executable, "-m", "yt_dlp", url, "--skip-download", "--print",
                        "%(duration)s|%(is_live)s|%(release_timestamp)s|%(timestamp)s|%(live_status)s"],
                       capture_output=True, text=True)
    try:
        dur, is_live, rel, ts, status = p.stdout.strip().splitlines()[-1].split("|")
    except Exception:
        log(f"  stream_info failed: {(p.stderr or '').strip()[-400:]}")
        return 0, False
    live = is_live == "True" or status == "is_live"
    LIVE["from_start"] = status in ("is_live", "post_live")
    if dur not in ("None", "NA", ""):
        return int(float(dur)), live
    start = rel if rel not in ("None", "NA", "") else ts
    if start not in ("None", "NA", ""):
        return int(time.time() - float(start)), live
    return 0, live


def vod_duration(url):
    return stream_info(url)[0]


HOOKS = {"chase": "COPS ON HIM", "crash": "HE CRASHED", "stunt": "INSANE STUNT", "combat": "SHOOTOUT",
         "npc_chaos": "THIS NPC WENT CRAZY", "funny_dialogue": "THIS DIALOGUE", "roleplay": "GTA RP MOMENT",
         "reaction": "HIS REACTION", "milestone": "MISSION MOMENT"}
# emoji only in YouTube titles (Arial Black in the video has no emoji -> boxes)
EMOJI = {"chase": "🚨", "crash": "💀", "stunt": "🤯", "combat": "🔫", "npc_chaos": "😭", "funny_dialogue": "😂",
         "roleplay": "🎭", "reaction": "😱", "milestone": "🔥"}
# stream housekeeping: never a clip (ads, sub/like begging, viewer counts)
HOUSEKEEPING = ["like button", "hit the like", "hit that like", "subscribe", "turn on the ads", "ads in a second",
                "ad break", "if you're new", "if you guys are new", "w's in the chat", "w in the chat",
                "hit that follow", "gifted", "thank you for the sub", "merch", "link in", "sponsor"]


def rolling_median(x, win):
    import numpy as np
    n, h, step = len(x), win // 2, 15
    c = list(range(0, n, step)) or [0]
    meds = [float(np.median(x[max(0, k - h):k + h + 1])) for k in c]
    return np.interp(np.arange(n), c, meds)


def windowed(arr, t, before, after):
    return arr[max(0, t - before):t + after]


def chunk_peaks(lines, loud, mot, game, offset, per_chunk=3):
    """Score one chunk -> list of (score, t_abs, cats, text). v2 (learned from the first Kai GTA 6 run):
    loudness is measured against the streamer's OWN rolling level (Kai is always loud), loud seconds
    with no words = screams/laughs, ad reads / like-begging are penalised, still screens (menus, map,
    phone) are penalised, and a pick needs at least 2 different signals."""
    import numpy as np
    n = len(loud)
    w = np.ones(10) / 10
    sm = lambda a: np.convolve(a, w, "same")
    excite = loud - rolling_median(loud, 180)  # dB above his usual level around then
    s_loud = sm(z(excite))
    s_mot = sm(z(mot))
    cover, rate = np.zeros(n), np.zeros(n)
    for l in lines:
        a_, b_ = int(l["start"]), int(min(n, max(l["end"], l["start"] + 1)))
        if a_ < n:
            cover[a_:b_] = 1
            rate[a_:b_] = np.maximum(rate[a_:b_], len(l["text"].split()) / max(0.5, l["end"] - l["start"]))
    s_laugh = sm(((excite > 6) & (cover == 0)).astype(float))  # loud, no words: scream / laugh / hype noise
    s_rate = sm(z(rate)) if rate.std() > 0 else np.zeros(n)
    score = s_loud + 0.5 * s_mot + 0.3 * s_rate + 2.0 * s_laugh
    words = HYPE + GAME_WORDS.get((game or "").lower(), [])
    cue, hype, house = np.zeros(n), np.zeros(n), np.zeros(n)
    cats_at = {}
    cf = None
    if (game or "").lower() == "gta":
        try:
            from gta_taxonomy import classify_text as cf
        except ImportError:
            cf = None
    for l in lines:
        t = int(l["start"])
        if t >= n:
            continue
        low = l["text"].lower()
        if any(x in low for x in HOUSEKEEPING):
            house[max(0, t - 5):int(l["end"]) + 15] = 1
            continue
        if any(x in low for x in NOT_NOW):
            continue
        hits = min(sum(1 for h in words if h in low), 3)
        score[max(0, t - 3):t + 7] += 0.4 * hits
        hype[max(0, t - 3):t + 7] += hits
        if cf:
            for c in cf(l["text"], roleplay_enabled=True):
                score[max(0, t - 8):t + 10] += 1.5 * c.strength
                cue[max(0, t - 8):t + 10] = np.maximum(cue[max(0, t - 8):t + 10], c.strength)
                cats_at.setdefault(t, []).append(f"{c.category}:'{c.phrase}'")
    score -= 3.0 * house
    still = sm((mot < np.percentile(mot, 25)).astype(float))  # menus, map, phone screens barely move
    score -= 2.5 * np.clip(still - 0.6, 0, 1)
    picked = []
    for t in np.argsort(score)[::-1]:
        if len(picked) >= per_chunk:
            break
        if not (30 < t < n - 30) or house[t]:
            continue
        sig = []
        if s_loud[t] > 0.7: sig.append("loud")
        if s_laugh[t] > 0.15: sig.append("scream/laugh")
        if s_mot[t] > 0.7: sig.append("action")
        if hype[t] >= 2: sig.append("hype words")
        if cue[t] > 0: sig.append("game event")
        if len(sig) < 2:
            continue
        if all(abs(t + offset - p) > 75 for _, p, _, _ in picked):
            near = [l["text"] for l in lines if t - 15 <= l["start"] <= t + 15]
            cats = sorted({c for k, v in cats_at.items() if t - 15 <= k <= t + 15 for c in v})
            picked.append((float(score[t]), int(t) + offset, cats + ["sig:" + "+".join(sig)], " ".join(near)[:200]))
    return picked


def make_hook(cats, said):
    """Hook on the first ~3 s: the event category, else the punchiest thing he said (2-6 words)."""
    order = ["chase", "crash", "stunt", "combat", "npc_chaos", "funny_dialogue", "milestone", "roleplay", "reaction"]
    found = {c.split(":")[0] for c in cats}
    for k in order:
        if k in found:
            return HOOKS[k]
    best, best_s = None, 0
    for ph in re.split(r"[.!?,]", said or ""):
        ws = ph.strip().split()
        low = ph.lower()
        if not (2 <= len(ws) <= 6) or any(x in low for x in HOUSEKEEPING + NOT_NOW):
            continue
        sc = sum(1 for h in HYPE if h in low) + (1 if "!" in ph else 0)
        if sc > best_s:
            best, best_s = " ".join(ws), sc
    return best.upper() if best else "WAIT FOR IT..."


def snap_window(lines, t_rel, offset, lead=6, payoff=20, min_len=20, max_len=38):
    """Start ~lead s before the peak, end ~payoff s after, snapped to whole transcript lines."""
    a, b = t_rel - lead, t_rel + payoff
    for l in lines:
        if l["start"] <= a <= l["end"]:
            a = l["start"]
        if l["start"] <= b <= l["end"]:
            b = l["end"]
    if b - a > max_len:
        b = a + max_len
    if b - a < min_len:
        b = a + min_len
    return offset + max(0, a) - 1, offset + b + 1


def media_seconds(path):
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    try:
        return float(p.stdout.strip())
    except ValueError:
        return 0.0


def ensure_server():
    """Start serve_clips.py (127.0.0.1:8766, clips folder only) unless it already runs."""
    import socket
    try:
        socket.create_connection(("127.0.0.1", 8766), timeout=1).close()
        return
    except OSError:
        pass
    flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0
    subprocess.Popen([sys.executable, str(Path(__file__).with_name("serve_clips.py"))],
                     creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log("SERVE started on 127.0.0.1:8766 (uploads)")


def make_title(job, streamer, cats, hook):
    """Factual starting title for YouTube (Claude rewrites it after watching the transcript)."""
    name = job.get("display_name") or streamer.split("-")[0].replace("kaicenat", "Kai Cenat")
    emo = next((EMOJI[c.split(":")[0]] for c in cats if c.split(":")[0] in EMOJI), "😳")
    return f"{name} {(hook or '').title()} {emo}"[:95]


def raw_cache(streamer, n):
    return WORK / "raw" / f"{streamer}_{int(n):02d}.mp4"


def keep_raw(raw, words, streamer, n):
    """Keep the 720p source + word timings for 4 days so a clip can be re-rendered
    (new hook / layout) without downloading or transcribing again."""
    dst = raw_cache(streamer, n)
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(raw, dst)
        dst.with_suffix(".words.json").write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        log(f"  could not keep source ({e})")
        Path(raw).unlink(missing_ok=True)


def clean_raw_cache(days=4):
    d = WORK / "raw"
    if d.exists():
        for f in d.iterdir():
            if time.time() - f.stat().st_mtime > days * 86400:
                f.unlink(missing_ok=True)


def write_best(out_dir, results, k=10):
    """best.json: the top-k clips by score (the ones to upload first)."""
    ok = [r for r in results if r.get("file") and Path(r["file"]).exists()]
    best = sorted(ok, key=lambda r: -(r.get("score") or 0))[:k]
    (out_dir / "best.json").write_text(json.dumps(best, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"STATUS BEST {len(best)} clips ranked -> {out_dir / 'best.json'}")


def heartbeat(mode):
    """agent_alive.txt goes quiet while agent.bat runs a job; busy_alive.txt shows the job is alive."""
    def beat():
        while True:
            try:
                Path("busy_alive.txt").write_text(f"{mode} {time.ctime()}", encoding="utf-8")
            except OSError:
                pass
            time.sleep(15)
    threading.Thread(target=beat, daemon=True).start()


def hunt(job_path):
    """Watch the stream part by part from the start at the lowest quality (only that part is
    downloaded, then deleted), clip each strong moment at 720p right away, continue to the end,
    and follow a live stream until it ends."""
    import numpy as np
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    if job.get("mode") == "serve":  # also reachable through GO_hunt.txt
        flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0
        subprocess.Popen([sys.executable, str(Path(__file__).with_name("serve_clips.py"))],
                         creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log("SERVE started")
        return
    url, game = job["url"], job.get("game", "")
    ensure_server()
    streamer = job.get("streamer", "stream")
    chunk = int(job.get("chunk_minutes", 20)) * 60
    max_clips = int(job.get("max_clips", 10))
    per_chunk = int(job.get("per_chunk", 1))
    min_score = float(job.get("min_score", 1.5))
    watch_h = int(job.get("watch_height", 144))
    out_dir = Path("clips") / f"{streamer}_{job.get('day') or dt.date.today().isoformat()}"
    WORK.mkdir(exist_ok=True)
    clean_raw_cache()
    total, LIVE["on"] = stream_info(url)
    LIVE["total"] = total if LIVE["on"] else 0
    if not total and job.get("duration"):
        total = int(to_sec(job["duration"]))
    log(f"== HUNT: {url} | {hms(total)} so far | {'LIVE' if LIVE['on'] else 'VOD'} | "
        f"{chunk // 60}-min parts at {watch_h}p, clips at {job.get('height', 720)}p, up to {max_clips} clips ==")
    scan_model = whisper_model(job.get("scan_model", "base"))
    made = int(job.get("start_n", 1)) - 1  # continue numbering after earlier clips
    max_clips += made
    info = out_dir / "clips_info.json"
    results = json.loads(info.read_text(encoding="utf-8")) if made and info.exists() else []
    t0 = int(to_sec(job.get("from", 0)))
    while made < max_clips and not Path("STOP").exists():
        t1 = t0 + chunk
        if t1 > total - (90 if LIVE["on"] else 0):  # not there yet
            if LIVE["on"] and job.get("follow_live", True):
                log(f"  waiting for the live to reach {hms(t1)} (now {hms(total)})")
                time.sleep(120)
                total, LIVE["on"] = stream_info(url)
                LIVE["total"] = total if LIVE["on"] else 0
                continue
            if t0 >= total - 20:
                break
            t1 = total  # last (shorter) part of a finished stream
        log(f"\n== Watching {hms(t0)}-{hms(t1)} at {watch_h}p ==")
        low = WORK / "live_watch.mp4"
        try:
            log(f"STATUS DOWNLOADING part {hms(t0)}-{hms(t1)} at {watch_h}p (only this part)")
            download_section(url, t0, t1, watch_h, low)
            log(f"STATUS WATCHING part {hms(t0)}-{hms(t1)} (listening + measuring action)")
            wav = WORK / "live_watch.wav"
            run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(low), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
            a = load_wav(wav)
            n = len(a) // 16000
            db = 20 * np.log10(np.sqrt((a[: n * 16000].reshape(n, 16000) ** 2).mean(axis=1)) + 1e-9)
            mot = motion_per_second(low, n)
            segs, _ = scan_model.transcribe(a, vad_filter=True)
            lines = [{"start": round(s_.start, 1), "end": round(s_.end, 1), "text": s_.text.strip()} for s_ in segs]
            low.unlink(missing_ok=True)
            wav.unlink(missing_ok=True)
            peaks = [p for p in chunk_peaks(lines, db, mot, game, t0, per_chunk) if p[0] >= min_score]
            try:  # Claude reads the part's transcript and picks (judge.py); auto picks if no answer
                import judge
                peaks = judge.decide(job, t0, t1, lines, db, mot, peaks)
            except ImportError:
                pass
            if not peaks:
                log(f"STATUS NOTHING good in {hms(t0)}-{hms(t1)}, moving on")
            for sc, t_abs, cats, said in peaks:
                if made >= max_clips:
                    break
                s0, s1 = snap_window(lines, t_abs - t0, t0)
                if any(not (s1 <= to_sec(r["start"]) or s0 >= to_sec(r.get("end", r["start"]))) for r in results):
                    log(f"  skip {hms(t_abs)}: overlaps a clip already made")
                    continue
                made += 1
                log(f"STATUS FOUND clip {made} at {hms(t_abs)} score={sc:.2f} cats={', '.join(cats) or '-'} | {said[:120]}")
                raw = WORK / f"live_raw{made:02d}.mp4"
                log(f"STATUS DOWNLOADING clip {made} {hms(s0)}-{hms(s1)} at {job.get('height', 720)}p")
                download_section(url, s0, s1, job.get("height", 720), raw)
                log(f"STATUS EDITING clip {made} (captions, facecam layout, hook, audio)")
                words = transcribe(raw, job.get("model", "small"))
                hook = make_hook(cats, " ".join(w["word"] for w in words)) if job.get("hook", True) else None
                out = out_dir / f"{streamer}_{made:02d}.mp4"
                fc, lay = job.get("facecam"), job.get("layout")
                try:  # Claude can choose the layout per pick ("fit" = blurred background, full frame)
                    import judge
                    ch = getattr(judge, "_CHOSEN", {}).get(t_abs) or {}
                    if ch.get("layout"):
                        lay = ch["layout"]
                        fc = ch.get("facecam") or (fc if lay == "facecam" else None)
                        log(f"  layout for this clip: {lay}")
                except ImportError:
                    pass
                render(raw, words, out, fc, lay, job.get("center_x", 0.5), hook)
                keep_raw(raw, words, streamer, made)
                results.append({"file": str(out), "start": hms(s0), "end": hms(s1), "score": round(sc, 2),
                                "why": ", ".join(cats) or "loud/motion/hype words", "hook": hook, "near": said,
                                "title": make_title(job, streamer, cats, hook),
                                "transcript": " ".join(w["word"] for w in words)})
                (out_dir / "clips_info.json").write_text(json.dumps(results, ensure_ascii=False, indent=1),
                                                         encoding="utf-8")
                log(f"STATUS READY clip {made} -> {out}")
        except Exception as e:
            log(f"  part failed: {e}")
        t0 = t1
        if not LIVE["on"] and t0 >= total:
            break
        if LIVE["on"]:
            total, LIVE["on"] = stream_info(url)
            LIVE["total"] = total if LIVE["on"] else 0
    write_best(out_dir, results)
    log(f"\nHUNT DONE: {made} clips in {out_dir.resolve()}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", default="job.json")
    ap.add_argument("--scan", help="VOD URL: watch it at ~160p and write scan/ (no clips)")
    ap.add_argument("--scan-model", default="base")
    ap.add_argument("--game", help="game profile for peak words, e.g. gta")
    ap.add_argument("--hunt", help="hunt job json: watch chunk by chunk, clip each peak right away")
    a = ap.parse_args()
    heartbeat("hunt" if a.hunt else "scan" if a.scan else "job")
    if a.hunt:
        hunt(a.hunt)
    elif a.scan:
        scan(a.scan, a.scan_model, a.game)
    else:
        mode = json.loads(Path(a.job).read_text(encoding="utf-8")).get("mode")
        if mode == "hunt":
            hunt(a.job)  # hunt sent through the job channel (job_log.txt)
        elif mode == "serve":  # start the local clip server in the background and return
            flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0  # DETACHED_PROCESS | NEW_PROCESS_GROUP
            subprocess.Popen([sys.executable, str(Path(__file__).with_name("serve_clips.py"))],
                             creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            log("SERVE started on http://127.0.0.1:8765")
        else:
            run_job(a.job)


if __name__ == "__main__":
    main()
