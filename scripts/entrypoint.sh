#!/usr/bin/env bash
set -euo pipefail

# Entry point for dev/prod containers
# - Optionally install spaCy model if not present
# - Adjust ownership of /app if HOST_UID/HOST_GID provided

# Install spaCy German model if missing with retries
python - <<'PY'
import importlib, sys, time, subprocess, os
from pathlib import Path
from dotenv import load_dotenv

# Robust .env loading: avoid calling load_dotenv() without args when running from
# an embedded stdin interpreter (which can cause find_dotenv to assert on stack frames).
# Prefer an explicit path if available, otherwise look for a .env in the current
# working directory and only call load_dotenv when a sensible path is found.
dotenv_path = os.getenv('DOTENV_PATH')
if not dotenv_path:
  p = Path('.') / '.env'
  if p.exists():
    dotenv_path = str(p.resolve())

if dotenv_path:
  try:
    load_dotenv(dotenv_path)
    print(f'Loaded .env from: {dotenv_path}')
  except Exception as e:
    print(f'Warning: failed to load .env from {dotenv_path}: {e}')
else:
  print('No .env file found; skipping load_dotenv()')
model = os.getenv("SPACY_DE_MODEL", "de_core_news_sm")
try:
  importlib.import_module(model)
  print(f'spaCy {model} model already installed')
except Exception:
  print(f'spaCy model {model} not found, attempting to download...')
  max_retries = 3
  for attempt in range(1, max_retries + 1):
    try:
      print(f'Attempt {attempt} to download {model}...')
      subprocess.check_call([sys.executable, '-m', 'spacy', 'download', model])
      print('Download successful')
      break
    except Exception as e:
      print(f'Attempt {attempt} failed: {e}')
      if attempt < max_retries:
        time.sleep(5 * attempt)
      else:
        print('All attempts to download spaCy model failed; continuing without model')
        break
PY

# # If HOST_UID and HOST_GID provided, chown app directory
# if [ -n "${HOST_UID:-}" ] && [ -n "${HOST_GID:-}" ]; then
#   echo "Setting ownership of /app to ${HOST_UID}:${HOST_GID}"
#   chown -R ${HOST_UID}:${HOST_GID} /app || true
# fi

# Exec the container CMD
exec "$@"
