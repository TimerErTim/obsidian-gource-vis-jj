#!/bin/env sh

cd "$(dirname "$0")" || exit 1

python3 -m venv .venv

source .venv/bin/activate || {
  echo "Failed to activate virtual environment"
  exit 1
}

pip install -r requirements.txt

