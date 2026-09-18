#!/usr/bin/env bash
set -euo pipefail

WHISPER_TAG="v1.9.4"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDOR="$ROOT/vendor/whisper.cpp"
MODEL_DIR="$ROOT/models/whisper"
MODEL_NAME="${KIRA_WHISPER_MODEL:-base}"
MODEL_PATH="$MODEL_DIR/ggml-${MODEL_NAME}.bin"

echo "KIRA local voice setup"
echo "Repository: $ROOT"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Error: this installer is for macOS." >&2
  exit 2
fi
if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "Warning: this profile is optimized for Intel x86_64; continuing on $(uname -m)." >&2
fi
for cmd in git cmake python3; do
  command -v "$cmd" >/dev/null || { echo "Error: missing $cmd" >&2; exit 2; }
done

python3 -m pip install --upgrade "webrtcvad-wheels==2.0.14"
mkdir -p "$(dirname "$VENDOR")" "$MODEL_DIR"
if [[ ! -d "$VENDOR/.git" ]]; then
  git clone --branch "$WHISPER_TAG" --depth 1 https://github.com/ggml-org/whisper.cpp.git "$VENDOR"
else
  git -C "$VENDOR" fetch --depth 1 origin "refs/tags/$WHISPER_TAG:refs/tags/$WHISPER_TAG"
  git -C "$VENDOR" checkout --detach "$WHISPER_TAG"
fi

cmake -S "$VENDOR" -B "$VENDOR/build" \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_METAL=OFF \
  -DWHISPER_BUILD_TESTS=OFF \
  -DWHISPER_BUILD_EXAMPLES=ON
cmake --build "$VENDOR/build" --config Release -j "$(sysctl -n hw.logicalcpu 2>/dev/null || echo 2)"

if [[ ! -f "$MODEL_PATH" ]]; then
  "$VENDOR/models/download-ggml-model.sh" "$MODEL_NAME" "$MODEL_DIR"
fi

BINARY="$VENDOR/build/bin/whisper-cli"
[[ -x "$BINARY" ]] || { echo "Error: whisper-cli was not built at $BINARY" >&2; exit 3; }
[[ -s "$MODEL_PATH" ]] || { echo "Error: model was not downloaded at $MODEL_PATH" >&2; exit 3; }

echo "Local voice installed. Verify with:"
echo "  python3 scripts/diagnose_local_voice.py"
