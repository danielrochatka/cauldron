#!/usr/bin/env bash
# Item 11: prove cauldron-cms-flatfile installs and passes its tests WITHOUT
# cauldron-workspace-flatfile in the environment. Creates a scratch venv,
# installs cauldron, cauldron-content, and cauldron-cms-flatfile from the
# repository, then runs the cms-flatfile test suite. Exits non-zero on any
# failure.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$(mktemp -d -t cauldron-cms-wheel-XXXXXX)/venv"
PY="${PYTHON:-python3}"

echo "[cms-wheel] creating scratch venv at ${VENV_DIR}"
"${PY}" -m venv "${VENV_DIR}"
# shellcheck disable=SC1090
source "${VENV_DIR}/bin/activate"

python -m pip install --upgrade pip >/dev/null

echo "[cms-wheel] installing cauldron, cauldron-content, cauldron-cms-flatfile only"
python -m pip install \
    -e "${REPO_ROOT}" \
    -e "${REPO_ROOT}/packages/cauldron-content" \
    -e "${REPO_ROOT}/packages/cauldron-cms-flatfile"

echo "[cms-wheel] verifying cauldron-workspace-flatfile is NOT installed"
if python -c "import cauldron_workspace_flatfile" 2>/dev/null; then
    echo "[cms-wheel] ERROR: cauldron_workspace_flatfile is importable in the scratch venv"
    deactivate || true
    exit 1
fi

echo "[cms-wheel] installing test dependencies"
python -m pip install pytest

echo "[cms-wheel] running cauldron-cms-flatfile tests"
python -m pytest "${REPO_ROOT}/packages/cauldron-cms-flatfile/tests" -q

echo "[cms-wheel] OK"
deactivate || true
