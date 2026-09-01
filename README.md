# ytgist

Paste a YouTube link, get the argument — not a wall of text.

Everything runs on your own machine. No API keys, no accounts, no per-minute billing, and
the audio never leaves the laptop.

```
YouTube link → yt-dlp (audio only) → Parakeet MLX → Qwen3.6 27B → a numbered argument
```

---

## What you get

A numbered argument rather than a bullet list. Each step is a headline that **states the
point** (not the topic), two to four short sentences that keep the reasoning, a timestamp
that links back into the video, and the speaker's own words underneath so you can check the
claim without watching.

Read only the bold headlines and you have the shape of the argument. Read the sentences and
you have why each step follows.

Also: **more detail** on any step, on demand, from that step's own passage of the
transcript. A library of everything you have ever summarised. Summaries in English *or* in
the video's own language, kept side by side. And an ETA that learns from your machine.

---

## Requirements

| | |
|---|---|
| **Mac** | Apple Silicon (M1 or newer). Intel will not work — the model runs on Metal. |
| **Memory** | Whatever you have — **pick the model to match**. 8 GB works. See below. |
| **Disk** | ~25 GB — 20 GB model, ~2 GB Python deps, plus transcripts. |
| **macOS** | Anything recent. Developed on macOS 26. |

### Pick the model for your RAM

**This matters more than anything else on the page.** Everything else is one download; this
is the difference between the app working and your Mac swapping.

| your Mac | model | download | what you get |
|---|---|---|---|
| **8 GB** | Qwen3.6 **1.7B** or **4B** Q4 | 1.1 / 2.5 GB | full 128k context, videos up to 3.8 h |
| **16 GB** | Qwen3.6 **4B** or **8B** Q4 | 2.5 / 4.9 GB | same |
| **32 GB** | Qwen3.6 **8B** or **14B** | 4.9 / 9 GB | same |
| **64 GB** | Qwen3.6 **27B** Q5 | 20 GB | same, and the best summaries |

The app **measures your RAM and the model's size and sizes its own context accordingly** —
a KV cache is proportional to both, so a 1.7B on an 8 GB Mac can afford a long context while
a 27B on the same machine can afford none. Choose too large a model for the machine and it
will not swap; it will refuse anything longer than a few minutes, which is the honest
version of the same limit.

Measured on the 64 GB machine this was built on, for reference: model resident 21.3 GB, KV
cache and compute buffers 3.2 GB at a 64k context, macOS and a browser another 8–10 — about
33–35 GB at peak, which is why the 27B wants 64.

**On 8 GB, download this instead of the 27B in step 2:**

```bash
hf download unsloth/Qwen3.6-4B-GGUF Qwen3.6-4B-UD-Q4_K_XL.gguf --local-dir ~/models
export YTGIST_MODEL=~/models/Qwen3.6-4B-UD-Q4_K_XL.gguf
```

Being straight about the trade: a 4B writes shorter, blunter takeaways than the 27B and is
more likely to fumble a timestamp. The structure holds; the prose is plainer. Transcription
quality is identical — that is Parakeet, and it is the same model at every size.

---

## Install

Four steps, start to finish. Homebrew must already be installed
([brew.sh](https://brew.sh)).

**1. Clone and run setup.**

```bash
git clone https://github.com/badgerhoneymoon/ytgist.git
cd ytgist
./setup.sh
```

This installs `yt-dlp`, `ffmpeg`, `llama.cpp`, `deno` and `node`, builds a Python
environment with `mlx` and `parakeet-mlx`, and runs `npm install`. Ten minutes or so, mostly
downloading. Safe to re-run.

It will finish by saying the model is **NOT FOUND** — that is expected, and step 2.

**2. Download the model** (~20 GB, once).

```bash
pip install huggingface_hub          # for the `hf` command, if you don't have it
mkdir -p ~/models
hf download unsloth/Qwen3.6-27B-GGUF Qwen3.6-27B-UD-Q5_K_XL.gguf --local-dir ~/models
```

Downloading the file from that page in a browser works just as well — put it in `~/models`.

Already have a GGUF elsewhere? Skip the download and point at it:

```bash
export YTGIST_MODEL=/path/to/your-model.gguf
```

(Put that line in your `~/.zshrc` if you want it to stick.)

**3. Check it found everything.**

```bash
./setup.sh
```

Run it again. It should now print `found 20G at …` instead of NOT FOUND.

**4. Start it.**

```bash
./ui
```

Then open **http://127.0.0.1:3210** and paste a YouTube link.

The first summary is slower than the app predicts — it downloads the Parakeet speech model
(~2.5 GB) the first time it transcribes anything.

---

## Run

```bash
./ui
```

That starts the engine on `:8765` and the interface on `:3210`, and prints both. Open
**http://127.0.0.1:3210**.

Prefer an app icon in the Dock?

```bash
./make_app.sh          # builds /Applications/ytgist.app
open -a ytgist
```

It launches both halves and opens a chromeless window. Logs go to
`~/Library/Logs/ytgist.log`.

There is a CLI too:

```bash
./ytgist "https://youtube.com/watch?v=…"
./ytgist "https://youtube.com/watch?v=…" --native   # summary in the video's own language
```

---

## What it will do the first time

The **first run downloads the Parakeet speech model** (~2.5 GB) from Hugging Face, so it is
slower than the estimate says. After that, first-summary-of-the-session costs ~10s to load
the 27B into memory; the server then stays warm for five minutes, so a second summary skips
it entirely.

Rough costs on an M4 Max, which the app measures and refines as you use it:

| video | time |
|---|---|
| under 20 min | ~1 min |
| about an hour | ~5 min |
| 2 hours | ~9 min |
| over 3.8 h | refused (see below) |

---

## Things that are deliberate

**The context is sized to the transcript.** 32k–128k on a ladder, with a q8_0 KV cache.
Splitting a long transcript in halves and merging them loses whatever connected the two, so
anything past ~3.8 hours is refused *before* the download rather than quietly downgraded.

**The ETA learns.** Every run records its real per-phase timings, video length, context
size, warm-or-cold and power mode to `~/.ytgist/runs.jsonl`. The estimate is a weighted fit
over recent runs, and predictions are stored beside outcomes so whether it is improving is
measurable rather than assumed:

```bash
$(./python-path) timing_log.py
```

**Low Power Mode is ~2.4× slower.** It is detected and learned separately rather than
averaged in.

**Quotes are shown exactly as recognised.** Two repair passes were built and both removed: a
full rewrite spent two minutes mostly adding commas, and a diff format "fixed" `Я не дан`
into `Я не даю` when the truth was `недавно`. A plausible wrong quote is worse than an
obviously garbled one.

**One run at a time.** The model is a single physical resource. Two concurrent runs each
started a 20 GB server and one's cleanup killed the other mid-generation.

**Transcripts are cached, audio is not.** Transcription is the expensive part and rarely
stale; the audio is deleted as soon as it has been read. Summaries are cached per language,
so switching the toggle never destroys the other version.

---

## Using a different model

Any GGUF llama.cpp can serve will work — the app only speaks to `llama-server` over HTTP.
Smaller means faster and less accurate:

```bash
export YTGIST_MODEL=~/models/Qwen3.6-8B-UD-Q5_K_XL.gguf
```

If you change model family, check `gist_prompt.py` — the prompts are tuned for Qwen3.6, and
the sampling parameters in `model_client.py` come from its model card.

---

## Troubleshooting

**`HTTP 403` on download.** YouTube refuses transiently. The app already retries three
times through different player clients. If it persists, `brew upgrade yt-dlp` and make sure
a JS runtime is installed (`brew install deno`) — without one, yt-dlp falls back to formats
that get refused.

**"No speech was found in that video."** Exactly that — a music video or a silent build
video has nothing to transcribe.

**Nothing at `:3210`.** The dev server died. `cd web && npm run dev` and read the error.

**The fan is loud.** Expected — the GPU is at 90 °C+ summarising. Install `macmon`
(`brew install macmon`) and the progress bar shows the temperature live.

**Everything is 2× slow.** Check Low Power Mode in System Settings → Battery.

---

## Layout

| file | what it owns |
|---|---|
| `youtube_ingest.py` | the only file that knows about yt-dlp — URLs, probing, audio, retries |
| `ytgist.py` | the pipeline, caching, timing, length limits |
| `model_client.py` | llama-server lifecycle, adaptive context, the warm pool |
| `gist_prompt.py` | every prompt, and the timestamp verifier |
| `timing_log.py` | the self-calibrating ETA |
| `gpu.py` | temperature and load, via macmon and ioreg |
| `serve.py` | HTTP + SSE for the web interface |
| `web/` | Next.js interface |

Built for one person's use, then handed to a second. No telemetry, nothing phones home.
