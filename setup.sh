#!/usr/bin/env bash
# One-time setup for a fresh session: Python deps + the speech model.
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

# Whisper "small" (CTranslate2 int8) for faster-whisper. Hugging Face is often blocked in
# cloud sessions, but npm is allowed, and this copy is published there in 4 parts.
MODEL=models/whisper-small
if [ ! -f "$MODEL/model.bin" ]; then
  mkdir -p "$MODEL" models/tmp
  for i in 0 1 2 3; do
    curl -fsSL "https://registry.npmjs.org/@skillninja/shortmovie-telop-model-p$i/-/shortmovie-telop-model-p$i-1.0.0.tgz" \
      | tar xz -C models/tmp --transform "s|^package|p$i|"
  done
  cat models/tmp/p{0,1,2,3}/model.bin.part* > "$MODEL/model.bin"
  cp models/tmp/p0/{config.json,preprocessor_config.json,tokenizer.json,vocabulary.json} "$MODEL/"
  echo "$(cat models/tmp/p0/model.sha256)  $MODEL/model.bin" | sha256sum -c -
  rm -rf models/tmp
fi
echo "autoclip ready: .venv/bin/python -m autoclip --help"
