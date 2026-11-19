# 🎬 Stream Clipper — AI that turns long livestreams into YouTube Shorts

> Paste a Twitch or YouTube stream URL → get **10 captioned, vertical (9:16) Shorts**, edited and uploaded to YouTube. No manual editing.

**Built 100% with AI.** I designed, debugged and shipped this whole project by directing [Claude](https://claude.ai) (Anthropic) — I did not hand-write the code. This README explains how I did it, because *how* I built it is the point.

---

## ✨ What it does

| Step | What happens |
|---|---|
| 📥 **Watch** | Reads the stream from 0:00 in 20-minute parts, at the **lowest quality** (144p/160p), downloading only that part's video fragments — never the whole stream |
| 👀 **Listen** | Transcribes each part with **faster-whisper** and measures loudness + motion |
| 🧠 **Judge** | An LLM reads the transcript and picks the real story moments (reactions, payoffs, fails), with a written reason for each pick |
| ✂️ **Edit** | Downloads only the picked moments in 1080p and renders a Short with **ffmpeg**: facecam on top / gameplay below (or blurred full-frame), word-by-word captions, a hook title, loudness normalization |
| ✅ **Check** | Reviews every clip (frame + transcript); redoes or drops weak ones |
| 🚀 **Upload** | Publishes the 10 best to YouTube Studio through browser automation, with title, description and credit to the streamer |

It also follows **live** streams until they end.

---

## 🤖 How I built it with AI (the real story)

I'm a 2nd-year computer engineering student. I knew the goal and the tools existed (yt-dlp, Whisper, ffmpeg), but not how to glue them into a reliable pipeline. So I treated the AI as my engineering team and myself as the **product owner + tester**:

1. **Defined the product** — "I give a URL, I get 10 good Shorts, I click nothing." I wrote rules: never download the whole stream, captions must never cut a sentence, always credit the streamer.
2. **Iterated on real streams** — I ran it on real VODs, watched the results, and reported what was wrong. Each run became a fix.
3. **Measured, then changed the approach** — The first version scored moments by loudness/motion/keywords. A backtest on a real stream showed it found only **2 of 10** good moments (it picked menus, cutscenes and "hit the like button" talk). The great clips were *story beats* only a transcript read catches → we switched to an **LLM-as-judge** design, keeping the old score only as a shortlist and an automatic fallback so the pipeline never stalls.
4. **Debugged real-world problems**, for example:
   - Whisper **hallucinates repeated lines over music** ("What?" ×40 during a cutscene) → the review step now collapses those runs and flags them.
   - The streamer's **webcam moves** during a long stream → auto facecam detection that must agree on 3 frames, else a safe fallback layout.
   - A blurry facecam → download at 1080p, keep the cam's real aspect ratio, denoise + Lanczos upscale + sharpen.
   - Uploading without a file picker failed because YouTube Studio blocks it (CSP, popups, localhost fetch) → documented what doesn't work and kept a working fallback.
   - A Windows `.bat` file breaking when edited while running → stop/edit/restart protocol.
5. **Turned it into a reusable "skill"** — all the knowledge (rules, lessons learned, fixes) lives in [`skill/SKILL.md`](skill/SKILL.md), so any Claude session can run the full pipeline from one command: `/stream-clipper`.

**What this shows:** I can take a vague idea to a working product with AI — breaking problems down, testing against real data, reading logs, spotting where the AI is wrong, and writing clear specs. That's the skill companies now need.

---

## 🧱 Tech stack

- **Python** — pipeline orchestration (`clipper.py`, `judge.py`)
- **yt-dlp** + HLS fragment fetching — partial downloads from Twitch/YouTube
- **faster-whisper** — speech-to-text with word timestamps
- **ffmpeg** — 9:16 rendering, ASS subtitles, `loudnorm`, denoise, upscaling, sharpening
- **LLM (Claude)** — moment selection, titles, quality review
- **Browser automation** — YouTube Studio upload
- **Windows agent** (`.bat` / `.vbs`) — runs hidden in the background, triggered by job files

## 🏗️ Architecture

```
 Claude (chat)                      User's PC (hidden agent)
 ─────────────                      ────────────────────────
 writes hunt.json  ───────────────▶ GO_hunt.txt → clipper.py --hunt
                                      │ per 20-min part:
                                      │  fetch fragments (160p) → transcribe → shortlist
 reads review/part_X.json ◀───────────┤  STATUS REVIEW (waits 240 s)
 writes part_X.picks.json ───────────▶│  cut picks @1080p → render Short
                                      │  STATUS READY clip n
 quality check / redo  ◀──────────────┘  ... → HUNT DONE → best.json
 uploads 10 best via browser
```

## 📁 Repository

```
ClickMeFirst.bat   ← one-click installer (Windows)
START_HERE.txt     ← the 3 last steps, opened for you after setup
skill/SKILL.md     ← the full skill: rules, pipeline, lessons learned, fixes
src/               ← clipper.py (download, transcribe, score, render), judge.py (LLM review),
                     serve_clips.py, Windows agent scripts
```

## 🚀 Setup (Windows, ~10 minutes)

**You need:** Windows 10/11 and the [Claude desktop app](https://claude.ai/download) on a paid plan. Everything else installs itself.

### 1. Download and run the installer
1. Click the green **Code → Download ZIP** button on this page, then **unzip** it.
2. Double-click **`ClickMeFirst.bat`**.
   *If Windows says "Windows protected your PC", click **More info → Run anyway**.*

It does everything for you:
- installs **Python 3.12** if you don't have it
- creates **`Videos\clipper`** and copies the code there
- installs **yt-dlp, faster-whisper, ffmpeg**
- builds **`stream-clipper-skill.zip`** for the Claude app
- starts the clipper agent hidden in the background (it also starts with Windows)
- opens the `clipper` folder and `START_HERE.txt`

### 2. Add the skill to Claude
In the Claude desktop app: **Settings → Skills** → upload **`stream-clipper-skill.zip`** from `Videos\clipper`.

### 3. Sign in to YouTube
In the Claude app, open the **built-in browser**, go to **studio.youtube.com** and sign in to the channel you'll post on. Claude never types your password.

### 4. Run it
Type **`/stream-clipper`** in a new chat.
- **First time only:** Claude asks **how many clips you want per video** (5, 10, 15, 20…). It's saved. Say *"change my clip count"* anytime.
- Click **Allow** when Claude asks for access to `Videos\clipper`.
- Choose **Clipping**, paste a Twitch or YouTube stream URL, and let it work. When each clip is ready, Claude opens the YouTube upload box and you drag the clip in.

**Optional:** with an NVIDIA GPU, double-click `install_gpu.bat` in `Videos\clipper` for faster transcription (~1 GB).

> Tuned for Kai Cenat's GTA streams (automatic facecam detection). For other streamers, Claude measures the webcam position or uses the blurred full-frame layout.

## ⚖️ Responsible use

Clips of other creators' streams can get copyright claims. The tool always credits the streamer, masks slurs in burned-in text, and keeps a video private if YouTube flags a claim. Ask the creator's permission when you can.

## 🙏 Credits

Scoring ideas inspired by [Clips Kitty](https://github.com/ColinGPT9/clips-studio); `gta_taxonomy.py` is an unchanged copy from its MIT-licensed [GTA plugin](https://github.com/ColinGPT9/Grand-Theft-Auto-6-Clips-Kitty-Plugin).

---

**Ghassen Chihi** — Computer Engineering student, ISIMA Mahdia 🇹🇳 · [GitHub](https://github.com/ghassenchihi) · ghassen.chihi.17@gmail.com
