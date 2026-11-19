---
name: "stream-clipper"
description: "Give Claude a stream URL (YouTube or Twitch, live or VOD) and get 10 excellent captioned 9:16 Shorts, uploaded, with no clicking: watch it from the start in small low-quality parts, Claude picks the moments from the transcript, cut at 720p, follow it to the end. Use when the user runs /stream-clipper or asks to clip a stream."
---

# Stream clipper

**You give a URL. Claude delivers 10 excellent clips, uploaded. You do no clicking.**

Watch the stream from 0:00 at the lowest quality, one 20-min part at a time (only that part's fragments are downloaded, then deleted) -> **Claude reads each part's transcript and picks the moments** (the score is only a shortlist and an automatic fallback) -> only those moments are downloaded at 720p -> edited into Shorts (9:16, facecam layout, word captions, hook, loudnorm) -> next part -> until the end (follows a live) -> quality check -> upload the 10 best to your channel, Public, with no file picker.

Everything runs on the user's PC through the hidden **clipper agent**; Claude starts jobs by writing files.

Ideas from Clips Kitty (github.com/ColinGPT9/clips-studio) and its MIT GTA plugin (github.com/ColinGPT9/Grand-Theft-Auto-6-Clips-Kitty-Plugin): multi-signal scoring, per-game event categories, lead-in/payoff, never cutting a sentence, an explainable reason per clip, factual titles.

## What we learned (keep it this way)
- Pure loudness/motion/word scoring found only **2 of 10** good moments in the backtest (Kai's GTA 6 premiere). It picked boat driving, garage/shop menus, game cutscene dialogue and "hit the like button" talk. The great clips were **story beats** only a transcript read catches: him panicking, "13 years bro", his honest verdict, a first-look reaction. So **Claude judges every part** (`judge.py`).
- Game cutscene / NPC dialogue is not the streamer: the game already subtitles it and nobody wants it. The clip must be **his** reaction.
- Burned-in captions/titles never show slurs (`judge.clean` masks them: n*****). Profanity is fine.
- Whisper (base, 144p audio) **hallucinates repeated short lines over music/cutscenes** (Speed GTA 6: "What?" x40 at 0:07:41 was really a cutscene). The review now collapses such runs and marks them `LIKELY WHISPER HALLUCINATION` - never pick a moment because of them. After each READY, compare the clip's 720p transcript (whisper small) with the pick; if it's mostly game dialogue, redo on a window that ends on his real reaction.
- The streamer's cam moves during a long stream (Kai's went from ~31% to ~50% height), so keep `"facecam": "auto"` for Kai's GTA room. The purple-light cam detector (3 frames per clip must agree, else "fit", never a box that grabs chat) only fits that room; other setups (green screen) fall back to "fit". Claude checks the first clip's frame and, when wrong, gives the box explicitly.

## The user's rules (always)
- **No clicking for him**, except the one-time `SETUP_once.vbs` (Step 1). Never ask him to pick, close or drag anything while the bridge works.
- **Never download the whole stream.** Watching = that part's fragments at the lowest quality (YouTube 144p, Twitch 160p, never "auto"), deleted after analysis. 720p only for the clip moments.
- **Watch the FULL stream from 0:00, in order, to the end.** Save every good clip (up to 3 per part); upload the **10 best** (`best.json`, Claude's picks rank first), in stream order; the rest stay saved. Live: keep following until it ends.
- **Tell him every step** (SendUserMessage, one short line + emoji, read from `STATUS` lines): 📥 part X-Y -> 👀 watching -> 🧠 picking from the transcript -> 🎯 clip N at H:MM:SS (what happens) / nothing good -> 📥 720p -> ✂️ editing -> ✅ ready (+ check) -> 🚀 uploaded (link).
- Edit: 9:16; facecam top + gameplay below when there is a cam (else center crop); 3-word captions, spoken word yellow; hook on the first ~3 s; `loudnorm`; whole sentences only.
- **Blur layout per clip, Claude decides (he asked, 2026-11-19, model clip `kaicenat-gta6_02`)**: `"layout": "fit"` = the WHOLE 16:9 frame (game + his cam overlay, nothing cropped) in the middle of the 9:16 canvas on a blurred, darkened copy; hook above, captions below. NOT for every clip: hunt.json keeps the split facecam layout as default, and Claude adds `"layout": "fit"` only to the picks where it's the better format - the moment needs the whole screen to make sense (cutscene/dialogue he reacts to, wide scene, chase seen from far, a map/phone screen he reacts to), the scene is dark, or the cam box is off/moving. Pure face reactions stay split (his face big). Foreground: `hqdn3d,scale=1080:-2:flags=lanczos,cas=0.4`.
- **Natural, sharp facecam (he asked, 2026-11-19)**: clips download at **1080p** (`"height": 1080`) so the small cam box has ~2x more pixels. The cam panel keeps the cam's own shape (height = 1080 x cam aspect, 540-780 px; the old fixed 1080x608 cut off Speed's head and chin), is trimmed 2% inside the box (6% at the bottom: overlay borders / light lines), denoised (`hqdn3d`), upscaled with `lanczos`, sharpened (`cas=0.6`), slightly more colour (`eq=saturation=1.06:contrast=1.03`), with a thin dark divider above the gameplay; gameplay gets lanczos + `cas=0.3`; encode `libx264 -preset medium -crf 18 -pix_fmt yuv420p`. In the quality check, look at the cam panel: whole head visible, no border line, not blurry; else fix the box and redo.
- **Excellent only**: check every clip (frame + transcript) and redo or drop weak/wrong ones.
- 10 clips per run unless he says otherwise. Channel: your channel (YouTube Studio, signed in inside the built-in browser). Visibility **Public**.
- Work folder: `%USERPROFILE%\Videos\clipper` (device_bash `$HOME/mnt/clipper`). Any .mp4 in `clips\` not under `_uploaded\` = not uploaded yet.
- Only one Claude chat drives the clipper at a time. If files change under you (mtime of `clipper.py`, `agent.bat`, new `GO_*`) that you didn't write, another chat is working: tell him and don't overwrite its files.

## Step 0 - Ask first: uploading or clipping? (he asked, 2026-11-19)
**Every run starts with this question, before anything else - even when a URL came with the command.** Use AskUserQuestion: **"Are we uploading or clipping today?"** with options **Uploading** (upload saved clips to your channel) / **Clipping** (clip a new live or video).
- **Uploading** -> folder check (below), list the clips not uploaded yet (one line per folder: streamer + count), ask which folder if there's more than one, then go straight to Step 5 (Step 1 only if the agent is needed). No URL needed.
- **Clipping** -> ask **"Send me the URL of the live or the video."** (plain text, wait for his answer; strip `?t=`). If the command already carried a URL, still ask, showing it: "Clip this one: <url>? Or send me another URL." Then the folder check, leftovers, and Step 1 onward. Wrong URL -> switch, leave old clips.

Then:
1. Folder not connected -> `device_request_folder_access` `~/Videos/clipper` (reason: "Run the clipper").
2. Leftovers (clipping only): `find $HOME/mnt/clipper/clips -name '*.mp4' -not -path '*/_uploaded/*' -not -path '*/_rejected/*' | sort` -> one line saying how many wait; `test` files: offer to delete.

## Step 1 - Agent (no clicks)
- Alive = `agent_alive.txt` OR `busy_alive.txt` (written every 15 s while a job runs) modified < 60 s ago (`stat -c %Y` vs `date +%s`).
- Not alive -> ask ONCE: **"Double-click `SETUP_once.vbs` in Videos\clipper - it starts the clipper hidden and with Windows, you never click again."** (it writes `clipper_agent.vbs` to his Startup folder and starts `agent.bat` with no window). If he can't, try computer use: File Explorer -> Videos -> clipper -> double-click `start_agent_hidden.vbs`; don't loop if Explorer ignores clicks.
- The agent: single instance (quits if another beat < 25 s ago), starts `serve_clips.py` (127.0.0.1:8766), then runs a job whenever `GO_hunt.txt` (`--hunt hunt.json` > `live_log.txt`) or `GO_job.txt` (`--job job.json` > `job_log.txt`) appears; `GO_update.txt` reinstalls packages; `GO_quit.txt` stops it. `STOP` ends a running hunt between parts.
- Never edit `agent.bat` while it runs (cmd reads .bat files as it goes - an edit breaks the loop). Write `GO_quit.txt`, wait for the heartbeat to stop, edit, then start it again.

## Step 2 - Read the stream + layout
Built-in browser (load the built-in-browser skill first). YouTube: `ytInitialPlayerResponse.videoDetails` (author, isLive) + `microformat.playerMicroformatRenderer.liveBroadcastDetails`. Twitch: title, `[data-a-target="player-seekbar-duration"]`, `h1`. One line to him: streamer, title, live/ended, length.
- **YouTube live still running**: can't be watched from the start in parts (feed lists only ~30 s; older pieces 403; re-confirmed 2026-11-19 on Speed's live: every part 'older than the rewind window'). Don't start a hunt from 0:00 on it. When he wants it from 0:00: write hunt.json now, then check every ~45 min with send_later until the replay is ready (post_live rules below), then `touch GO_hunt.txt`. Other options: the Twitch VOD of the same stream (Kai simulcasts) or clipping from now on.
- **YouTube private/unavailable**: say so, offer Twitch.
- **Normal YouTube upload (not a stream replay, `isLiveContent` false, no hlsManifestUrl)**: no HLS feed, so parts use `--download-sections` (~1.3x real time for the first part, much faster later) - tell him ~15 min per 20-min part. Fan re-uploads (e.g. SpeedUniverse) carry extra claim risk: say so once.
- **YouTube just ended (post_live)**: wait until `endTimestamp` is set, `isLiveNow` false, `lengthSeconds` full, `streamingData.hlsManifestUrl` present.
- Layout (hunt.json default; blur is chosen per pick): Kai GTA purple room -> `"facecam": "auto"`; other setups -> measure the cam box `[x, y, w, h]` with `"layout": "facecam"` (Speed GTA 6: `[0.0, 0.43, 0.2, 0.23]`); full-screen cam (just chatting) -> `"layout": "center"`.

## Step 3 - Hunt
Write `hunt.json` with a device_bash heredoc (`cat > $HOME/mnt/clipper/hunt.json <<'EOF'`; never device_commit_files - it writes stale copies), check with `cat`, then `touch GO_hunt.txt`. A hunt already running -> `touch STOP`, wait for `HUNT DONE`.
```json
{"url": "<url>", "streamer": "<name>-<topic>", "display_name": "Kai Cenat", "game": "gta",
 "from": "0:00:00", "chunk_minutes": 20, "per_chunk": 3, "max_clips": 60, "min_score": 2.0,
 "watch_height": 160, "height": 1080, "model": "small", "scan_model": "base",
 "layout": "auto", "facecam": "auto", "hook": true, "follow_live": true,
 "judge": "claude", "judge_wait": 240}
```
- New `streamer` per stream. Continue an interrupted hunt with `"from": "<where it stopped>"` + `"start_n": <next number>`.
- `live_log.txt` locked by a frozen run -> same JSON as `job.json` with `"mode": "hunt"` + `touch GO_job.txt` (log `job_log.txt`).
- Peaks inside one part must be > 75 s apart in ABSOLUTE stream time; overlapping windows are skipped automatically.
- Optional extra signal for Twitch VODs: most-viewed viewer clips (`twitch.tv/<login>/clips?filter=clips&range=7d` -> clip -> "Watch Full Video" -> `?t=`) - mention them in your picks reasoning. Never call gql.twitch.tv yourself (blocked).
- A clip download can fail with a one-off `HTTP error 403 Forbidden` from googlevideo (the hunt logs `part failed` and moves on): re-queue just that clip with a `job.json` redo (`"n"`, `"fresh": true`, its start/end/hook/title) + `touch GO_job.txt` - the agent runs it after the hunt.
- `part failed` -> read the lines above it, fix clipper.py (only when no other chat is editing it), `touch STOP`, then `touch GO_hunt.txt` again with `from` set to where it stopped.
- Per part: fragment-only download (`hls_section`: `yt-dlp -g` -> media m3u8 -> only the fragments covering the part, 8 in parallel -> join -> trim; fallback `--download-sections`, with `--live-from-start` for YouTube lives), loudness + motion + transcript (whisper base), auto shortlist (`chunk_peaks` v2: loudness vs his own rolling level, screams/laughs, motion, hype words, GTA cues; penalties for ad/sub talk and still menu screens), YouTube "Most replayed" bonus, then the **review step**.

### The review step (Claude = judge) - the heart of quality
Each part logs `STATUS REVIEW part A-B -> review\<streamer>\part_H-MM-SS.json` and waits `judge_wait` s (240) for `part_H-MM-SS.picks.json`; no answer -> automatic picks (never stalls).
Poll the log every ≤ 2 min while hunting (sleep inside device_bash, < 170 s per call): `grep -a "STATUS\|failed\|DONE\|EXIT" $HOME/mnt/clipper/live_log.txt | tail -5`. On a new REVIEW, read it:
```bash
cd $HOME/mnt/clipper && python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(d['part']);[print('AUTO',c['t'],c['score'],c['why'],'|',c['said'][:120]) for c in d['auto_candidates']];print('REPLAYED',d['most_replayed']);[print(*l) for l in d['transcript']]" "review/<streamer>/part_H-MM-SS.json"
```
Pick up to 3 moments, write the answer with a heredoc, quickly:
```bash
cat > $HOME/mnt/clipper/review/<streamer>/part_H-MM-SS.picks.json <<'EOF'
[{"start": "0:10:12", "end": "0:10:44", "hook": "13 YEARS BRO", "title": "Kai Cenat waited 13 years for this 😭", "why": "GTA 6 finally starts, he screams"}]
EOF
```
(`[]` = nothing good). Add `"layout": "fit"` to a pick when the blur format suits it better (see the user's rules); picks without it use hunt.json's layout. Picking rules:
- **Yes**: his big reactions (scream, laugh, panic, disbelief), a story with a payoff, a funny line of his, chaos he causes in game (chase, crash, death, NPC fight) *while he reacts*, a verdict/opinion, a milestone ("13 years"), a fail.
- Reaction streams: his own reactions over someone else's footage. Muted parts (flat loudness, Twitch DMCA) are skipped.
- GTA categories (`gta_taxonomy.py`): chase, crash, stunt, combat, npc_chaos, funny_dialogue, roleplay, reaction, milestone; hypotheticals, plans, past stories, retractions and negations don't count.
- **No**: cutscene / NPC dialogue without him, menus/garage/shopping/map, quiet driving, ads, sub/like/follow talk, viewer counts, past stories with no payoff, hypotheticals.
- Window 20-45 s: start 1 line before the setup, end right after the payoff; whole sentences (use the transcript times; +/-2 s margin).
- Hook: 2-5 words, caps, no emoji, no spoilers of the punchline. Title: factual, punchy, < 90 chars, `<Streamer> <what happens> <1 emoji>`, no hashtags.
- Prefer quality over count: a part with nothing great gets `[]`.

## Step 4 - Quality check (excellent only)
After each `STATUS READY clip n`: device_bash preview (`ffmpeg -ss 10 -i clip -frames:v 1 -vf scale=180:-1`, `hstack` a few clips) -> stage only that jpg; read the clip's transcript/title in `clips_info.json`.
- Wrong layout (chat in the cam box, menu, blur everywhere) -> redo via `job.json` `{"url", "streamer", "redo": true, "sections": [{"start", "end", "n", "layout": "fit" | "facecam", "facecam": [x,y,w,h], "hook"}]}` + `touch GO_job.txt` (720p source is cached in `clipper_work\raw` for 4 days).
- Weak clip -> move it to `clips\<folder>\_rejected\` (mv works without delete permission) and note it.
- Fix `title` in `clips_info.json`/`best.json` when the auto title is vague ("Kai Cenat His Reaction 😳").
- At `HUNT DONE` (`STATUS BEST` -> `best.json`): tell him "N clips ready" + one line per best clip, then go straight to Step 5.

## Step 5 - Upload
Built-in browser, YouTube Studio (your channel). For each of the 10 best (stream order):
1. Studio -> Create -> "Upload videos": open it with JS (`#create-icon` click, wait 1 s, then the `tp-yt-paper-item` whose text is "Upload videos") and confirm the dialog text has "Drag and drop" (a ref click once left only the menu open).
2. He drags the file in: SendUserMessage "Upload box is open (Ctrl+Shift+B). Drag `<folder>\<file>.mp4` (k of N)" and poll for `ytcp-video-metadata-editor` with `browser_batch` (4 x `computer wait 10` + one JS check; `await setTimeout` loops in javascript_tool hang here), ~3 min; not in time -> "N clips still waiting - I'll remind you next time". Check the default title (`speed gta6 03`) matches the clip you asked for.
   - **The no-drag bridge does NOT work in the built-in browser** (tested 2026-11-19): Studio blocks the hidden iframe (CSP), `fetch` to 127.0.0.1 ("Failed to fetch"), and every popup - even one opened from a real click. `serve_clips.py` itself works (`http://127.0.0.1:8766/list` opens fine in its own tab). Don't retry those. Untested ideas for a future no-drag upload: Claude in Chrome's `file_upload` (3.8 MB max -> needs a small upload copy), or computer use on the Windows file picker (type the path in File name + Enter).
3. Details (proven recipe): never click the textbox refs (in the narrow pane a click landed in the description and ctrl+a wiped it). Focus by JS: `tb=[...document.querySelectorAll('ytcp-uploads-dialog #textbox')]; tb[i].scrollIntoView({block:'center'}); tb[i].focus(); getSelection().selectAllChildren(tb[i])` -> key Delete -> type (i=0 title, i=1 description); read both back before going on. No emoji with a variation selector (✈️) in typed titles - typing silently fails; use 😭 😂 🐯 🤯 etc. Kids: JS-click `tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]` (check aria-checked). Then JS-click `ytcp-uploads-dialog #next-button` 3 times with 2 s waits, JS-click `[name="PUBLIC"]` and `#done-button`; "Publish anyway" via find ref; the link is in `ytcp-video-share-dialog a[href*="youtube.com/shorts"]`, then click its `#close-button`.
   Content: title, description (lines typed separately with `Return`): what happens / `Full stream: <link>` / `All credit to <Streamer>. Clip for entertainment.` / blank / `#<Streamer> #<Game> #Shorts #Gaming #StreamHighlights` + 1-2 topical. Never type `#` in the title. Never press Escape in the dialog.
4. "No, it's not made for kids" -> Next x3 -> Public -> Publish. "Still checking... you may get a strike" -> AskUserQuestion once per run (wait / publish anyway / keep private); claim/block found -> Private, tell him.
5. Link from "Video published" (`a[href*="youtube.com/shorts"]`) -> **delete the file** (his rule since 2026-11-19): `device_request_delete_permission` on the clipper folder once per session (reason "Delete each clip after it's uploaded to YouTube"), then `rm` right after its link appears - never an unpublished clip. Declined -> move to `clips\_uploaded\<folder>\`.
- Report: each title + link; Private/claimed ones; clips still waiting; whether the hunt is still running.

## Fixes
- Exit code 3221225786 (0xC000013A) = the agent's window was closed / Ctrl+C. The hidden agent (`SETUP_once.vbs`) avoids it; continue with `from` + `start_n`.
- `metadata_errors` TypeError (PyAV): whisper gets a float32 array (`load_wav`), never a path.
- `cublas64_12.dll not found`: CPU is the default; `install_gpu.bat` (~1 GB, ask first) + `CLIPPER_GPU=1` enables the GPU.
- YouTube "No supported JavaScript runtime": `touch GO_update.txt` (installs `yt-dlp[default]` + `deno`).
- `--download-sections` (ffmpeg) is slow (~1.3x real time on Twitch) - that's why `hls_section` fetches fragments directly.
- Twitch chat replay (chat-downloader) fails with `'data'` KeyError retries forever - never use it.
- `clipper_work` grows (an old whole-stream `stream.mp4` was 3.2 GB): when he allows deletion, clear `clipper_work\*.mp4|*.wav` older than a day (not `raw\`, it's the 4-day re-render cache).

## Limits (say plainly)
- Never type his passwords; he signs in himself in the built-in browser.
- Vivaldi is view-only; Terminal/File Explorer are click-only for computer use.
- The cloud sandbox and device_bash VM can't reach Twitch/YouTube: everything runs through the agent on Windows.
- Motion/loudness can't see the screen; the transcript can't hear tone. Say when a pick is a judgement call.
- Reposting other streamers' clips can get claims or strikes; always credit the streamer.

## Files (rebuild only if missing)
- `clipper.py`: download (`hls_section`, `download_section`), whisper (`load_wav`, `whisper_model`, `transcribe`), `stream_info` (duration, live, post_live); `write_ass` (3 words/line, Arial Black 88, spoken word yellow `{\c&H00FFFF&}`, outline 6, + boxed hook for the first 3.2 s); `render(src, words, out, facecam, layout, center_x, hook)` (per-pick layout: `judge._CHOSEN[mid]` keeps the pick's `layout`/`facecam` and `hunt` uses them over the job's; fit: blurred `boxblur=20:2,eq=brightness=-0.06` 9:16 background + `hqdn3d,scale=1080:-2:flags=lanczos,cas=0.4` full frame centered, captions at 1290, hook at 330; facecam: `media_dims` -> cam_h from the trimmed box's aspect (540-780), cam `crop(trimmed box),hqdn3d=1.5:1.5:4:4,scale=1080:cam_h:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:cam_h,cas=0.6,eq=saturation=1.06:contrast=1.03,drawbox` divider over gameplay `crop=ih*1080/game_h:ih,scale=1080:game_h:flags=lanczos,cas=0.3`, captions at cam_h, hook at cam_h+150; center: `crop=ih*9/16:ih:iw*cx-ih*9/32:0,hqdn3d,scale lanczos,cas=0.5`; fit: blurred 9:16 background + full frame centered; audio `loudnorm=I=-14:TP=-1.5:LRA=11`; ffmpeg cwd = output folder; libx264 medium crf 18 yuv420p, aac 160k, +faststart; previous versions backed up as `clipper_backup_v2.py`, `clipper_backup_v3.py`; `judge_backup_v1.py`); `chunk_peaks` v2, `make_hook`, `make_title`, `snap_window`, `keep_raw` cache, `write_best`, `heartbeat`, `hunt`, `run_job` (`redo`), `scan` (only when asked). Calls `judge.decide(...)` right after `peaks = ...` in `hunt`.
- `judge.py`: review files + waiting for Claude's picks + fallback; YouTube most-replayed; slur masking (patches `write_ass`, `snap_window`, `make_hook`, `make_title` of the running clipper).
- `serve_clips.py` (127.0.0.1:8766, clips only, `/list`, `/bridge.html`), `upload_inject.js` (Studio side; blocked in the built-in browser, kept for a future browser that allows it).
- `agent.bat`, `SETUP_once.vbs`, `start_agent_hidden.vbs`, `install_gpu.bat`, `gta_taxonomy.py` + `LICENSE_gta_taxonomy.txt` (MIT, unchanged copy of the plugin's `src/gta_highlights/taxonomy.py`; re-download if missing). CRLF line endings for .bat/.vbs.