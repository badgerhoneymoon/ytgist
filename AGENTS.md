# AGENTS.md — instructions for an AI coding agent

You are helping someone install and run **ytgist** on their own Mac, or modify it. This file
is the operational one; `README.md` is written for a person reading top to bottom.

Read this whole file before running anything. Several steps look like failures and are not.

---

## What this is

A local pipeline: `yt-dlp` pulls audio only → Parakeet (MLX) transcribes with timestamps →
a GGUF model behind `llama-server` writes a numbered argument → a Next.js page renders it.

Two processes, both local:

| process | port | started by | what it owns |
|---|---|---|---|
| engine (Python) | **8765** | `./serve` | ingestion, transcription, the model, HTTP + SSE |
| web (Next.js) | **3210** | `cd web && npm run dev` | the interface only |

`./ui` starts both.

---

## Install: run these in order, verify each

**Do not skip the verifications.** Each one catches a failure that otherwise surfaces
minutes later as something misleading.

### 1. Tools and environment

```bash
./setup.sh
```

Verify:

```bash
for c in yt-dlp ffmpeg llama-server deno node; do command -v $c >/dev/null && echo "ok $c" || echo "MISSING $c"; done
./python-path                                    # prints the interpreter that will run the engine
$(./python-path) -c "import parakeet_mlx, mlx; print('python deps ok')"
```

**`setup.sh` ends by printing the model as `NOT FOUND`. That is expected**, not an error — it
declines to download 20 GB unprompted. Continue to step 2.

### 2. Choose and download a model — THE decision that matters

Pick by the user's RAM. Getting this wrong is the difference between working and unusable:

| RAM | model | file |
|---|---|---|
| 8 GB | Qwen3.6 4B Q4 | `unsloth/Qwen3.6-4B-GGUF` → `Qwen3.6-4B-UD-Q4_K_XL.gguf` |
| 16 GB | Qwen3.6 4B or 8B | `unsloth/Qwen3.6-8B-GGUF` |
| 32 GB | Qwen3.6 8B or 14B | `unsloth/Qwen3.6-14B-GGUF` |
| 64 GB | Qwen3.6 27B Q5 | `unsloth/Qwen3.6-27B-GGUF` → `Qwen3.6-27B-UD-Q5_K_XL.gguf` |

Check the RAM first, do not assume:

```bash
python3 -c "import subprocess;print(round(int(subprocess.run(['sysctl','-n','hw.memsize'],capture_output=True,text=True).stdout)/1e9),'GB')"
```

Then:

```bash
pip install huggingface_hub
hf download unsloth/Qwen3.6-4B-GGUF Qwen3.6-4B-UD-Q4_K_XL.gguf --local-dir ~/models
export YTGIST_MODEL=~/models/Qwen3.6-4B-UD-Q4_K_XL.gguf     # add to ~/.zshrc to persist
```

Verify the app agrees with your choice:

```bash
$(./python-path) -c "
import sys; sys.path.insert(0,'.'); import model_client as m, ytgist as y
print('model  :', m.MODEL)
print('context:', m.ctx_ceiling()//1024, 'k')
print('longest:', round(y.max_minutes()/60,1), 'h')"
```

**If `context` prints `8k` and `longest` prints ~0.1 h, the model is too large for the
machine.** The app is refusing rather than swapping. Go one size down.

### 3. Start and verify

```bash
./ui                     # foreground; starts engine + web
```

From another shell:

```bash
curl -s -o /dev/null -w "engine %{http_code}\n" http://127.0.0.1:8765/api/history
curl -s -o /dev/null -w "web    %{http_code}\n" http://127.0.0.1:3210/
```

Both must be `200`. Then open http://127.0.0.1:3210.

### 4. First real run

Use a short video with speech. **The first transcription downloads Parakeet (~2.5 GB)**, so
run one is much slower than the app's own estimate. This is not a bug; do not "fix" it.

---

## Operating rules — violate these and you will destroy work

**Never restart the engine while a job is running.** A summary in flight is lost, and the
next click silently pays for it again. Always check first:

```bash
curl -s http://127.0.0.1:8765/api/current | python3 -c "import sys,json;c=json.load(sys.stdin);print('RUNNING' if c.get('job') and not c.get('result') else 'idle')"
```

**Restarting is not `pkill; ./serve`.** SIGTERM waits up to 120s for an in-flight job, so it
holds port 8765 while it finishes. Wait for the port:

```bash
pkill -f "ytgist/serve.py"; sleep 4
while nc -z 127.0.0.1 8765 2>/dev/null; do sleep 2; done
./serve > /tmp/ytgist-serve.log 2>&1 &
```

**Editing a file under `web/` hot-reloads the user's page.** If a run is in flight the page
may drop its live view. The run itself survives; the view rejoins via `/api/current`.

**Python changes require an engine restart to take effect.** Committing is not deploying. If
you hold a restart because a job is running, say clearly which changes are not live yet —
forgetting this produced an entire round of "the fix didn't work".

**An idle `llama-server` is not a leak.** It is parked deliberately for 45–300s (short on
small-RAM machines) so the next request skips the model load. It reaps itself.

---

## Invariants — do not "simplify" these away

- **One run at a time.** `_RUN` in `serve.py`. Two concurrent runs each start a multi-GB
  server and the orphan sweep kills the other's mid-generation.
- **`run.last` is cleared at the start of every run.** It is a function attribute that
  survives calls; an early return that leaves it set publishes the *previous video's summary*
  under the new video's title.
- **Timestamps are verified against the transcript.** `gist_prompt.verify` drops invented
  ones and keeps the text. Never let a model-supplied timestamp reach the UI unchecked.
- **Audio is deleted after transcription; transcripts are kept.** Transcription is the
  expensive part; the audio is not ours to hoard.
- **Summaries are cached per language.** English and native are separate files. A change that
  makes them share one destroys the other.
- **The context ceiling is computed, not constant** (`model_client.ctx_ceiling`). It reads
  the machine's RAM and the model's file size. Hardcoding it back breaks small machines.

---

## Failure modes, with the exact strings

| what you see | what it is | what to do |
|---|---|---|
| `YouTube refused the download (HTTP 403)` | transient; YouTube rate-limits per IP | already retried 3× across player clients. Retry later. Ensure `deno` is installed and `/opt/homebrew/bin` is first on PATH. |
| `No speech was found in that video.` | music video / silent footage | correct behaviour, not a bug |
| `That video is N hours long…refuses` | transcript exceeds the context ceiling | use a smaller model (bigger ceiling) or a shorter video |
| `Requested format is not available` | YouTube served an inconsistent format list | transient; retry |
| page stuck, no error | usually the dev server died | `cd web && npm run dev`, read the error |
| everything ~2.4× slow | macOS Low Power Mode | System Settings → Battery. The app detects and learns it separately. |

---

## Where things live

| path | what |
|---|---|
| `~/.cache/ytgist/` | transcripts and summaries (JSON) |
| `~/.ytgist/runs.jsonl` | one row per run: timings, prediction, RAM/power context |
| `~/.ytgist/expands.jsonl` | one row per "more detail" click |
| `/tmp/ytgist-serve.log` | engine log |
| `~/Library/Logs/ytgist.log` | the .app launcher's log |

| module | responsibility |
|---|---|
| `youtube_ingest.py` | the **only** file that knows yt-dlp exists |
| `ytgist.py` | pipeline, caching, phases, length limits |
| `model_client.py` | llama-server lifecycle, context sizing, warm pool |
| `gist_prompt.py` | every prompt + timestamp verification |
| `timing_log.py` | the self-calibrating ETA |
| `gpu.py` | temperature/load via macmon and ioreg |
| `serve.py` | HTTP + SSE |

---

## If you are asked to modify it

- **Run `./node_modules/.bin/tsc --noEmit` and `npx eslint app` in `web/` after any UI edit.**
  `npx tsc` may resolve to an unrelated package; use the local binary.
- **Prompts are tuned for Qwen3.6.** Sampling parameters in `model_client.py` come from its
  model card — `top_p 0.80, top_k 20, min_p 0.0, presence_penalty 1.5`. llama.cpp's defaults
  disagree on four of five, so they are sent explicitly. Do not remove them.
- **Prefer mechanical enforcement over prompt instructions** for surface rules. A mid-size
  model obeys "never write X" perhaps 80% of the time; a regex obeys it always. See
  `_destaff` in `ytgist.py`.
- **Rule count matters more than rule wording.** The expand prompt went from nine rules to
  four because a rule sitting seventh was being ignored outright.
