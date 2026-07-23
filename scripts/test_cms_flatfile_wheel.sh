#!/usr/bin/env bash
# Item 13: build real wheels for cauldron-content and cauldron-cms-flatfile,
# install them into a fresh venv OUTSIDE the source tree, and prove the
# CMS-flatfile package works without cauldron-workspace-flatfile installed.
# Exits nonzero on any failure.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="/tmp/cauldron_cms_test_venv"
WHEEL_DIR="$(mktemp -d -t cauldron-cms-wheels-XXXXXX)"
PY="${PYTHON:-python3}"

echo "[cms-wheel] cleaning old venv at ${VENV_DIR}"
rm -rf "${VENV_DIR}"

echo "[cms-wheel] creating fresh venv at ${VENV_DIR}"
"${PY}" -m venv "${VENV_DIR}"
# shellcheck disable=SC1090
source "${VENV_DIR}/bin/activate"

python -m pip install --upgrade pip build >/dev/null

echo "[cms-wheel] building wheels into ${WHEEL_DIR}"
python -m build --wheel --outdir "${WHEEL_DIR}" "${REPO_ROOT}/packages/cauldron-content" >/dev/null
python -m build --wheel --outdir "${WHEEL_DIR}" "${REPO_ROOT}/packages/cauldron-cms-flatfile" >/dev/null

echo "[cms-wheel] installing wheels from ${WHEEL_DIR}"
# Install ONLY the wheels + PyPI transitive deps. No editable installs.
# No PYTHONPATH into the source tree.
python -m pip install --find-links "${WHEEL_DIR}" \
    cauldron-content cauldron-cms-flatfile >/dev/null

echo "[cms-wheel] verifying cauldron-workspace-flatfile is NOT installed"
if python -c "import cauldron_workspace_flatfile" 2>/dev/null; then
    echo "[cms-wheel] ERROR: cauldron_workspace_flatfile is importable in the scratch venv"
    deactivate || true
    exit 1
fi

echo "[cms-wheel] confirming FlatFileRepository is importable and instantiable"
python -c "from cauldron_cms_flatfile.repository import FlatFileRepository; print('ok')"

echo "[cms-wheel] running minimal instantiation smoke test"
TMP_SITE="$(mktemp -d -t cauldron-cms-site-XXXXXX)"
mkdir -p "${TMP_SITE}/content/pages" "${TMP_SITE}/schemas"
python -c "
from pathlib import Path
from cauldron_cms_flatfile.config import FlatFileCMSConfig
from cauldron_cms_flatfile.repository import FlatFileRepository
cfg = FlatFileCMSConfig(site_root=Path('${TMP_SITE}'))
repo = FlatFileRepository(cfg)
descriptor = repo.describe()
assert descriptor.provider_name == 'flatfile', descriptor
print('smoke test ok')
"
rm -rf "${TMP_SITE}"

echo "[cms-wheel] installing pytest and running package tests"
python -m pip install pytest >/dev/null
python -m pytest "${REPO_ROOT}/packages/cauldron-cms-flatfile/tests" -q

echo "[cms-wheel] OK"
deactivate || true
