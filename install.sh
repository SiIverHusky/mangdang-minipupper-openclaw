#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

echo "Installed openclaw-app dependencies."
echo "Next: cp config.example.yaml config.yaml and edit values."
