#!/bin/bash
# One-time setup on macOS. Run from the repo folder:  bash scripts/setup_mac.sh
set -e
cd "$(dirname "$0")/.."

if ! command -v brew >/dev/null 2>&1; then
  echo ">> Installing Homebrew (you will be asked for your Mac password)"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv)"
fi

echo ">> Installing Python 3 and ffmpeg"
brew install python@3.12 ffmpeg >/dev/null

echo ">> Creating virtual environment"
python3 -m venv .venv
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

echo ">> Running tests"
python -m pytest -q tests || true

cat <<'MSG'

Done. Every time you open a new terminal, run these two lines first:
  cd "$(pwd)"
  source .venv/bin/activate

Then, for example:
  python -m otd_shorts.cli plan --date tomorrow
  OTD_RENDERER=local python -m otd_shorts.cli render --date tomorrow --channel price_action --limit 1
  open data/videos/*/
MSG
