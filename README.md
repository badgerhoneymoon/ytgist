# ytgist

Paste a YouTube link, get the argument — not a wall of text.

Everything runs on this machine. Audio never leaves it, no API keys, no per-minute billing.

```
YouTube link → yt-dlp (audio only) → Parakeet MLX → Qwen3.6 27B → numbered argument
```

## What it produces

A numbered argument rather than a bullet list. Each step is a headline that **states the
point** (not the topic), two to four short sentences that keep the reasoning, a timestamp
that links back into the video, and the speaker's own words underneath so you can check
the claim without watching.

Read only the bold headlines and you get the shape of the argument. Read the sentences and
you get why each step follows.

## Running it

Needs `yt-dlp`, `llama-server` (llama.cpp), a Qwen3.6 27B GGUF in `~/models/`, and Node 20+.

```bash
./serve                 # the engine on :8765 — ingestion, transcription, the model
cd web && npm run dev   # the interface on :3210
```

Or `./make_app.sh` to build `ytgist.app`, which starts both and opens a chromeless window.

CLI, if you prefer:

```bash
./ytgist "https://youtube.com/watch?v=…"
./ytgist "https://youtube.com/watch?v=…" --native   # summary in the video's own language
```

## Things that are deliberate

**The context is sized to the transcript.** 32k–128k on a ladder, with a q8_0 KV cache.
Splitting a long transcript in halves and merging them loses whatever connected the two,
so anything past ~3.8 hours is refused before the download rather than quietly downgraded.

**The ETA learns.** Every run records its real per-phase timings, video length, context
size and power mode to `~/.ytgist/runs.jsonl`. The estimate is the median of matching runs.
Predictions are stored beside outcomes, so whether it is improving is measurable rather
than assumed — `python3 timing_log.py`.

**Images resolve through Wikidata, which can refuse.** The model names the subject and its
type (`Yabloko | political party`); Wikidata is an entity database, so it answers "no such
thing" for `Apple party`. Free-text search cannot — Commons matched that string to a photo
of Hallowe'en apple-bobbing, and Wikipedia's search offers "Apples to Apples". A step whose
subject cannot be resolved exactly gets no picture.

**Quotes are shown as recognised.** Two repair passes were built and both were removed: a
full rewrite spent two minutes mostly adding commas, and a diff format "fixed" `Я не дан`
into `Я не даю` when the truth was `недавно`. A plausible wrong quote is worse than an
obviously garbled one.

**One run at a time.** The model is a single physical resource. Two concurrent runs each
started a 20 GB server and one's cleanup killed the other mid-generation.

**Transcripts are cached, audio is not.** Transcription is the expensive part and rarely
stale; the audio is deleted as soon as it has been read. Summaries are cached per language,
so switching the toggle never destroys the other version.

## Layout

| file | what it owns |
|---|---|
| `youtube_ingest.py` | the only file that knows about yt-dlp; URL parsing, probing, audio |
| `ytgist.py` | the pipeline, caching, timing, length limits |
| `model_client.py` | llama-server lifecycle, adaptive context, orphan cleanup |
| `gist_prompt.py` | every prompt, and the timestamp verifier |
| `entities.py` | Wikidata resolution for step images |
| `timing_log.py` | the self-calibrating ETA |
| `serve.py` | HTTP + SSE for the web interface |
| `web/` | Next.js interface |

Built for one person's use. No telemetry, nothing phones home.
