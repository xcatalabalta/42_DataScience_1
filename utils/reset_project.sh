#!/bin/sh
./reset_db.py --yes
# absolute path to present script
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# its parent = repo root
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"
echo "Working from repository root: ${REPO_ROOT}"
rm -rf data
ls -lR
