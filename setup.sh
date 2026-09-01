#!/bin/bash
# One-shot setup for a fresh machine. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")"

say() { printf "\n\033[1m%s\033[0m\n" "$1"; }

say "1/4  Command-line tools"
for f in yt-dlp ffmpeg llama.cpp deno node; do
  brew list "$f" >/dev/null 2>&1 && echo "  $f already installed" || brew install "$f"
done
brew list macmon >/dev/null 2>&1 || echo "  macmon (optional, for the temperature readout): brew install macmon"

say "2/4  Python environment"
# parakeet-mlx pulls mlx, which is large. If a sibling install already has it, say so
# rather than downloading it twice.
if [ -x "../dictate/.venv/bin/python" ] && ../dictate/.venv/bin/python -c "import parakeet_mlx" 2>/dev/null; then
  echo "  reusing ../dictate/.venv, which already has mlx + parakeet-mlx"
else
  [ -d .venv ] || python3 -m venv .venv
  ./.venv/bin/pip install --quiet --upgrade pip
  ./.venv/bin/pip install --quiet "parakeet-mlx>=0.5" "mlx>=0.31"
  echo "  .venv ready"
fi
echo "  engine will run: $(./python-path)"

say "3/4  Web interface"
(cd web && npm install --silent)

say "4/4  The model"
MODEL="${YTGIST_MODEL:-$HOME/models/Qwen3.6-27B-UD-Q5_K_XL.gguf}"
if [ -f "$MODEL" ]; then
  echo "  found $(du -h "$MODEL" | cut -f1) at $MODEL"
else
  echo "  NOT FOUND: $MODEL"
  echo "  Download it (~19GB), then re-run this script:"
  echo
  echo "    mkdir -p ~/models && cd ~/models"
  echo "    hf download unsloth/Qwen3.6-27B-GGUF \\"
  echo "      Qwen3.6-27B-UD-Q5_K_XL.gguf --local-dir ."
  echo
  echo "  Or point at one you already have:  export YTGIST_MODEL=/path/to/model.gguf"
fi

say "Done.  Start it with:  ./ui"
