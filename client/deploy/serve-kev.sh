#!/bin/sh
# Serve kev-4B as a System One decision service on the GPU machine.
#
# Verified facts this script encodes (docs/design-handoff.md sections 8 and 10):
#   * only kev-4B: bf16 needs ~8-9 GB of VRAM (a 9B would need ~18 GB - do not try it on a 16 GB card)
#   * KEV_MERGE=0 is REQUIRED on a consumer card. With merging on, checkpoint.py builds the
#     backbone in fp32 and DecisionModel.__init__ ends with self.to(device) - so an 18.6 GB
#     fp32 model is moved onto the card and a 12-16 GB card dies with CUDA OOM. Off, the model
#     is built in KEV_DTYPE (bf16, 9.3 GB) and fits. The cost is that the adapter stays
#     unmerged: README's max|dp| is 0.029 unmerged vs 0.017 merged-and-cast. On >=24 GB, set
#     KEV_MERGE=1.
#   * on CUDA/ROCm the Qwen3.5 bases need flash-linear-attention, or latency suffers
#     (on Windows the official triton has no wheel: install triton-windows first)
#   * calibration ships ON: each checkpoint stores a fitted temperature (~2.1-2.4) that the
#     pointer head applies at load time. Do NOT set KEV_TEMPERATURE=1.0.
#   * hf.co is not reachable from CN networks; hf-mirror is. The first run downloads
#     the adapter plus the Qwen3.5 base.
#   * the first request pays a model-load cost, so warm the service up once after boot
#
# Usage:  ./serve-kev.sh [port]        (default 8009)
set -eu

PORT="${1:-8009}"
KEV_DIR="${KEV_DIR:-$HOME/kev}"
RUN="${KEV_RUN:-jaredpalmer/kev-4b}"

export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export KEV_DTYPE="${KEV_DTYPE:-bf16}"
export KEV_MERGE="${KEV_MERGE:-0}"

if [ ! -d "$KEV_DIR" ]; then
  git clone https://github.com/jaredpalmer/kev.git "$KEV_DIR"
fi
cd "$KEV_DIR"

uv sync --extra serve

# flash-linear-attention matters on CUDA/ROCm for the Qwen3.5 bases (the server logs
# "falling back to its reference PyTorch implementation" without it). On Windows the
# official triton has no wheel, so the third-party triton-windows goes in first.
if command -v nvidia-smi >/dev/null 2>&1; then
  case "${OS:-}$(uname -s 2>/dev/null)" in
    *Windows*|*MINGW*|*MSYS*)
      uv pip install triton-windows || echo "warn: triton-windows install failed; flash-linear-attention needs it" ;;
  esac
  uv pip install flash-linear-attention || echo "warn: flash-linear-attention install failed; latency may suffer"
fi

echo "starting kev on port $PORT (run=$RUN, dtype=$KEV_DTYPE)"
uv run --extra serve python -m kev.serve --run "$RUN" --port "$PORT" &
SERVE_PID=$!
trap 'kill $SERVE_PID 2>/dev/null || true' EXIT INT TERM

# Warm up: the first request loads the model, and a cold first request looks like an outage.
for _ in $(seq 1 60); do
  sleep 2
  if curl -sf "http://127.0.0.1:$PORT/v1/models" >/dev/null; then
    curl -s "http://127.0.0.1:$PORT/v1/systemone" -H 'content-type: application/json' \
      -d '{"state":"warmup","questions":{"q":{"type":"noul","instructions":"is this a warmup"}}}' >/dev/null
    echo "ready: http://127.0.0.1:$PORT/v1/systemone"
    break
  fi
done

wait $SERVE_PID

# ---------------------------------------------------------------------------
# Keeping it resident
#
# Linux (systemd), /etc/systemd/system/bixian-kev.service:
#   [Unit]  Description=kev-4B decision service
#           After=network-online.target
#   [Service]
#           User=<you>
#           Environment=HF_ENDPOINT=https://hf-mirror.com
#           Environment=KEV_DTYPE=bf16
#           WorkingDirectory=<KEV_DIR>
#           ExecStart=/usr/bin/env uv run --extra serve python -m kev.serve --run jaredpalmer/kev-4b --port 8009
#           Restart=always
#           RestartSec=5
#   [Install] WantedBy=multi-user.target
#
# Windows: Task Scheduler -> "At startup", run this script (or the uv command)
# with "Run whether user is logged on or not". Then point clients at
# DECIDE_URL=http://<lan-ip>:8009.
#
# Degradation matrix (each consumer must implement its own row):
#   agent-internal judgment -> fall back to its own model
#   irreversible gate       -> fail closed, or ask a human
#   batch job               -> queue and retry
#   service down > N min    -> ALERT; never silently degrade to "always allow"
# ---------------------------------------------------------------------------
