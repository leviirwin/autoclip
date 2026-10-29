#!/usr/bin/env bash
# One-time setup for a fresh session: Python deps + the speech model.
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
# Pre-download the transcription model so the first transcribe doesn't stall.
.venv/bin/python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"
echo "autoclip ready: .venv/bin/python -m autoclip --help"
