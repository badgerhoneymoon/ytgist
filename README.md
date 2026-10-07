# ytgist

Paste a YouTube link, get the argument — not a wall of text.

Everything runs on your own machine. No API keys, no accounts, no per-minute billing, and
the audio never leaves the laptop.

```
YouTube link → yt-dlp (audio only) → Parakeet MLX → Qwen3.6 27B → a numbered argument
```

Also: **research mode** (a topic → up to 10 videos that are about it → one brief across all of them),
and the same engine on **a Linux / WSL2 PC with an NVIDIA GPU**, which the Mac uses
automatically whenever it's on. See [Research mode](#research-mode) and
[Running on an NVIDIA PC](#running-on-an-nvidia-pc-linux--wsl2).

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

Not sure what your Mac can take? **[canirun.ai](https://www.canirun.ai/)** detects your
machine and ranks the open models that fit. One caveat when using it here: calculators like
that size for the model weights plus a short chat context, while ytgist feeds it a whole
transcript and needs the KV cache to match. **Treat their answer as the ceiling and go one
size down** if you want long videos.

**On 8 GB, download this instead of the 27B in step 2:**

```bash
hf download unsloth/Qwen3.6-4B-GGUF Qwen3.6-4B-UD-Q4_K_XL.gguf --local-dir ~/models
export YTGIST_MODEL=~/models/Qwen3.6-4B-UD-Q4_K_XL.gguf
```

Being straight about the trade: a 4B writes shorter, blunter takeaways than the 27B and is
more likely to fumble a timestamp. The structure holds; the prose is plainer. Transcription
quality is identical — that is Parakeet, and it is the same model at every size.

---

> **Handing this to an AI coding agent?** Point it at
> **[AGENTS.md](AGENTS.md)** instead — same install, written as a checklist with a
> verification after every step, plus the operating rules and failure modes that a person
> picks up by feel and an agent has to be told.

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

## Local API and durable queue

The existing engine serves this API at **http://127.0.0.1:8765**. It stays on loopback;
there is no second service, account, API key, or additional dependency.

Submit a JSON object to `POST /api/jobs` (or the existing `POST /api/gist`):

```json
{"url":"https://www.youtube.com/watch?v=VIDEO_ID","native":false}
```

The response remains `{"job":"…"}`. Optional fields are `model` (`dense` or `coder`),
`native`, `refresh`, `regen`, and `shots`. The flags default to false. Invalid requests
return HTTP 400 before enqueueing. The existing screenshot endpoint, `POST /api/frames`,
uses the same durable queue and requires `url`, matching `video` ID, and optional `native`.
`POST /api/expand` remains synchronous and shares the same model lock.

| endpoint | purpose |
|---|---|
| `GET /api/jobs?limit=50` | newest jobs, with status; limit 1–100 |
| `GET /api/jobs/JOB_ID` | request, status, cumulative progress, result or error |
| `GET /api/jobs/JOB_ID/result` | saved final event; 409 until succeeded, 404 for unknown ID |
| `GET /api/events?job=JOB_ID` | SSE latest progress and final result, independently for each reader |
| `POST /api/cancel` with `{"job":"JOB_ID"}` | cancel queued or running work; returns `{"ok":true}` when accepted |
| `GET /api/video?v=VIDEO_ID&native=0` | cache-only transcript and English summary; `native=1` selects original language |

The cache-only endpoint never probes YouTube, runs a model, or creates work. It returns
timestamped `sentences`, availability/staleness flags, and `result.raw` with the saved
summary plus expansions and screenshots. Missing videos return 404. Invalidated cache
versions are reported explicitly rather than exposing stale timestamps as usable output.

Jobs and results live in `~/.ytgist/jobs.sqlite3` (override with `YTGIST_JOBS_DB`). A single
dispatcher processes jobs in submission order. Summaries, screenshots, and detail requests
still share the existing processing lock. A second engine cannot open the same job store.
`/api/current` reports the active summary; enqueueing another video cannot replace it.

Statuses are `queued`, `running`, `cancelling`, `succeeded`, `failed`, `cancelled`, and
`interrupted`. Queued work survives engine restarts and resumes automatically. Work that
was running when the engine stopped becomes `interrupted`; submit a new request explicitly
to retry it. Completed results remain retrievable across restarts. Cancellation during
processing takes effect at the pipeline's existing checkpoints; it does not undo work
already saved to the video cache.

Identical active submissions from the UI and another client share one job. For retries
after a lost response or completed job, send an `Idempotency-Key` header of 1–200 characters.
Reuse that key only for the same normalized request: it always returns the original job,
including after restart, and a changed request returns 409. Use a new key to retry a failed
or interrupted job. A submission without a key after completion creates a new job and
uses the normal video cache. URL tracking parameters and omitted default flags do not
create different requests.

SSE sends revision IDs and cumulative progress; it supports `Last-Event-ID` and replays the
saved terminal event to late readers. Intermediate updates may be coalesced, so use job
status/result endpoints as the durable source of truth. Stream directly from the engine,
not through the Next.js proxy. The UI polls its own job ID; another client's video cannot
replace its result. It also supports the previous engine until the next safe restart.

Offline regression checks (no listeners, network, model runs or subprocesses):

```bash
python3 -m unittest discover -p 'test_integration.py' -v
```

---

## Research mode

A topic instead of a link: the most useful videos on it, each summarised, then **one brief
across all of them**, laid out like a single video's gist.

```
topic → 4 YouTube searches → model scores every result → up to 10 gists (+ screenshots) → one brief
```

1. **Search.** The model rewrites your topic into three short queries of 2–6 words, the way
   people type them: the subject alone, the subject plus its most important detail, and a
   practitioner's or case-study angle. (YouTube matches long, stacked queries badly.) Plus
   your topic as typed, that makes four. yt-dlp runs YouTube's own search for each, metadata
   only, with approximate upload dates, and the results are pooled: about 40–50 candidates,
   minus shorts, live streams and anything too long to summarise.
2. **Pick.** The model names the topic's subject and scores every result from its title and
   description: 3 = about the subject and its details, 2 = the subject with one detail loosened,
   1 = only near it (general advice from the field, reviews, idea lists), 0 = off. Code then
   picks up to 10 from the 3s and 2s, and **never fills up with 1s**: a niche topic gets 3
   good videos, not 10 with 7 off-topic. If fewer than 6 qualify, it searches once more with
   three broader queries and scores again. On fast-changing topics (tools, models, prices, ad
   rules) videos 2+ years old go last and 3+ years old are left out. At most 2 per channel.
   The page shows the subject, how many fit or came close, and each video's age.
3. **Gist.** Each pick is an ordinary job, so it lands in the library, uses the cache and can be
   opened on its own. Screenshots are a tick box, off by default: on an RTX 5090 they add about
   2 minutes per video. The page shows both estimates, from this machine's own measurements.
4. **Combine.** One brief:
   - a TL;DR, then 8–12 numbered takeaways
   - each with a headline that states the point, a few sentences, and the moments it rests on
     (`V3 12:34`)
   - **what was said** at those moments, taken from the transcript, never from the model
   - the screenshot that video found for that moment, if any

   Below that, collapsed: where they agree, where they disagree, what only one says, and which
   to watch first. Every citation is checked against that video's summary; a timestamp it
   doesn't have is dropped, and the source stays.

Open it from the **Research** link at the top of the page. One research runs at a time.

| endpoint | purpose |
|---|---|
| `POST /api/research` `{"topic", "n": 10, "shots": false, "native": false}` | start → `{"id"}`; 409 while one is running |
| `GET /api/research?limit=20` | recent runs and the active one |
| `GET /api/research/ID` | status, the queries, the picks, then `report` |
| `POST /api/research/ID/drop` `{"video"}` | leave one video out |
| `POST /api/research/ID/cancel` | stop |
| `POST /api/research/ID/recombine` | write the brief again from the finished summaries |
| `GET /research/ID` · `/research/ID.md` | the brief as a standalone page, or as markdown |

Runs live in `~/.ytgist/research/` (JSON + markdown).

Measured on an RTX 5090: 10 videos in about 5 minutes plus about a minute to combine, or about
half an hour with screenshots.

---

## Running on an NVIDIA PC (Linux / WSL2)

The same engine runs on Linux with an NVIDIA GPU. When `parakeet-mlx` isn't installed, it uses
the same Parakeet weights through NVIDIA NeMo. The model's context is sized to the card's
VRAM, every layer goes on the GPU, and screenshots crop with Pillow instead of macOS `sips`.
Measured on an RTX 5090 for a 15-minute video:

- transcription: 22 s, including loading Parakeet
- summary: 9 s, plus a 13 s model load

Tested on Ubuntu 24.04 under WSL2.

**1. System packages + CUDA toolkit** (the toolkit is only needed to build llama.cpp):

```bash
sudo apt install ffmpeg cmake ninja-build unzip build-essential
# WSL2: NVIDIA's wsl-ubuntu repo (no driver; Windows provides it). Native Linux: the ubuntu repo.
wget https://developer.download.nvidia.com/compute/cuda/repos/wsl-ubuntu/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb && sudo apt update && sudo apt install cuda-toolkit-13-0   # or newer, up to your driver
```

**2. llama.cpp with CUDA.** Set the architecture for your card (`120` = RTX 50-series):

```bash
git clone --depth 1 https://github.com/ggml-org/llama.cpp ~/ai/llama.cpp && cd ~/ai/llama.cpp
PATH=/usr/local/cuda/bin:$PATH cmake -B build -G Ninja -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=120 -DLLAMA_CURL=OFF
cmake --build build --target llama-server -j
```

**3. Python environment.** Install torch first and pin it, or NeMo's resolver may pull a
different build:

```bash
uv venv --python 3.12 ~/ai/ytgist/.venv
uv pip install --python ~/ai/ytgist/.venv/bin/python torch torchaudio --index-url https://download.pytorch.org/whl/cu130
~/ai/ytgist/.venv/bin/python -c "import torch;print('torch=='+torch.__version__)" > /tmp/pin.txt
uv pip install --python ~/ai/ytgist/.venv/bin/python "nemo_toolkit[asr]" "transformers>=4.50" "yt-dlp[default]" \
  --constraint /tmp/pin.txt --extra-index-url https://download.pytorch.org/whl/cu130 --index-strategy unsafe-best-match
curl -fsSL https://deno.land/install.sh | sh        # yt-dlp needs a JS runtime for YouTube
```

`transformers>=4.50` is not optional. Without it, the resolver walks back to a 2021
`transformers` whose tokenizers need a Rust compiler to build.

**4. Model.** Download the GGUF (and the projector, `mmproj-F16-Qwen3.6-27B.gguf`, for
screenshots) to a Linux path, not `/mnt/c`: WSL reads Windows drives at about 0.3 GB/s.

**5. Run it as a service.** Use [`deploy/ytgist.service`](deploy/ytgist.service): edit the
paths, then `systemctl --user enable --now ytgist`.

**Using it from the Mac.** Put the PC engine's address in `~/.ytgist/remote`:

```bash
echo "http://my-pc:8766" > ~/.ytgist/remote
```

`./ui` and the `.app` then use the PC whenever it answers, and the Mac's own engine when it
doesn't. The page shows which: **on the PC** or **on this Mac**. The two machines keep separate
libraries. `YTGIST_ENGINE=mac ./ui` forces the Mac.

**Sharing the GPU.** If other things use the same card, set `YTGIST_SHED` to a booking service
(see [`shed.py`](shed.py) for the four endpoints). ytgist books the GPU before each job, waits
while someone else holds it, and holds nothing on the GPU when idle.

| variable | default | what |
|---|---|---|
| `YTGIST_MODEL` / `YTGIST_MMPROJ` | `~/models/…` | the GGUF and its vision projector |
| `YTGIST_HOST` / `YTGIST_PORT` | `127.0.0.1` / `8765` | where the engine listens |
| `YTGIST_IDLE` | 300 s (45 on small machines) | how long a finished model server stays parked |
| `YTGIST_NEMO_IDLE` | 90 s | how long Parakeet stays loaded between videos (CUDA) |
| `YTGIST_SHED` | unset | GPU booking service, if the card is shared |
| `~/.ytgist/remote` or `YTGIST_REMOTE` | unset | the Mac launcher's remote engine |

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
| `gpu.py` | temperature and load, via macmon and ioreg (nvidia-smi on a PC) |
| `research.py` | research mode: search, pick, combine, the standalone brief page |
| `shed.py` | optional booking of a shared GPU |
| `serve.py` | HTTP + SSE for the web interface |
| `deploy/ytgist.service` | systemd unit for a Linux / WSL2 PC |
| `web/` | Next.js interface |

Built for one person's use, then handed to a second. No telemetry, nothing phones home.
