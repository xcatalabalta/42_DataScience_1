#!/bin/sh

set -eu

ARCHIVE="${HOME}/data_postgres/subject.zip"
FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

# --- Precondition 1: operate from the repository root, wherever we were called
# The script lives in utils/, one level below the repo root. Resolve both from
# the script's own location so the caller's current directory does not matter.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"   # absolute path
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"   # its parent = repo root
cd "${REPO_ROOT}"
echo "Working from repository root: ${REPO_ROOT}"

# --- Precondition 2: the archive must be present
if [ ! -f "${ARCHIVE}" ]; then
    echo "ERROR: archive not found at ${ARCHIVE}" >&2
    echo "Download it first (see VM-instructions.txt)." >&2
    exit 1
fi

# --- Precondition 3: unzip must be available
if ! command -v unzip >/dev/null 2>&1; then
    echo "ERROR: 'unzip' not installed. Run: sudo apk add unzip" >&2
    exit 1
fi

rm -rf customer item data items

# --- Extract to a temp dir, then move the inner folders into place
TMPDIR=".subject_extract_tmp"
rm -rf "${TMPDIR}"
mkdir -p "${TMPDIR}"

echo "Extracting ${ARCHIVE} ..."
unzip -q -o "${ARCHIVE}" -d "${TMPDIR}"

if [ ! -d "${TMPDIR}/subject/customer" ] || [ ! -d "${TMPDIR}/subject/item" ]; then
    echo "ERROR: archive layout unexpected (no subject/customer or subject/item)." >&2
    rm -rf "${TMPDIR}"
    exit 1
fi

mkdir -p data/customer
# mv "${TMPDIR}/subject/customer" "data/customer"
mv "${TMPDIR}/subject/item" "data/items"
rm -rf "${TMPDIR}"

# --- Report
echo "Done."
echo "data/customer/ :"
ls -1 data/customer
echo "data/items/ :"
ls -1 data/items
