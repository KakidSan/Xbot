#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ -d "${PWD}/.deps" ]]; then
  export PYTHONPATH="${PWD}/.deps${PYTHONPATH:+:${PYTHONPATH}}"
fi
if [[ -x "${PWD}/bin/xbot" ]]; then
  exec -a xbot "${PWD}/bin/xbot" --config "${PWD}/config.yaml"
fi
if [[ -x "${PWD}/.venv/bin/python" ]]; then
  exec -a xbot "${PWD}/.venv/bin/python" "${PWD}/xbot.py" --config "${PWD}/config.yaml"
fi
exec -a xbot python3 "${PWD}/xbot.py" --config "${PWD}/config.yaml"
