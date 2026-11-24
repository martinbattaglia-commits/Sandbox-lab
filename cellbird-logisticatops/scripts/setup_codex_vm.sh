#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
export SELLBIRD_HOME="$project_dir"
export TMPDIR="$project_dir/.runtime/tmp"
export npm_config_cache="$project_dir/.runtime/npm-cache"
mkdir -p "$TMPDIR"

sellbird_commit=b212385259da92246a56311d790d936e75dd70dd
codex_version=0.159.3
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Se requiere Python 3.11 o posterior.")'
if [[ ! -x .venv/bin/python ]]; then
    python3 -m venv .venv
fi
export PATH="$project_dir/.venv/bin:$project_dir/.runtime/codex-cli/node_modules/.bin:$PATH"

if ! python - "$sellbird_commit" <<'PY'
from importlib.metadata import PackageNotFoundError, distribution
import json
import sys

try:
    package = distribution("sellbird")
except PackageNotFoundError:
    sys.exit(1)
origin = json.loads(package.read_text("direct_url.json") or "{}")
sys.exit(0 if package.version == "0.4.0" and origin.get("vcs_info", {}).get("commit_id") == sys.argv[1] else 1)
PY
then
    python -m pip install --force-reinstall \
        "sellbird @ git+https://github.com/ethanplusai/sellbird.git@$sellbird_commit"
fi
python -m pip check

codex_binary="$project_dir/.runtime/codex-cli/node_modules/.bin/codex"
if [[ ! -x "$codex_binary" ]] || [[ "$("$codex_binary" --version)" != "codex-cli $codex_version" ]]; then
    npm install --prefix "$project_dir/.runtime/codex-cli" --no-audit --no-fund "@openai/codex@$codex_version"
fi

if [[ ! -e sellbird.local.yaml && ! -L sellbird.local.yaml ]]; then
    ln -s cellbird.local.yaml sellbird.local.yaml
fi
if [[ ! sellbird.local.yaml -ef cellbird.local.yaml ]]; then
    printf '%s\n' 'sellbird.local.yaml debe apuntar a cellbird.local.yaml.' >&2
    exit 1
fi

# Validate the effective config before preparing the paused sandbox.
python - <<'PY'
from sellbird.config import load_config

cfg = load_config()
email = cfg.channels.email
if email.enabled or not email.require_approval or email.max_daily_sends != 0:
    raise SystemExit("El sandbox requiere correo deshabilitado, revisión manual y cupo cero.")
PY
sellbird sending pause
sellbird signals --confirm free
"$codex_binary" --version
python - <<'PY'
from importlib.metadata import version
import platform

print(f"Python {platform.python_version()} | Sellbird {version('sellbird')} | MCP {version('mcp')}")
PY
