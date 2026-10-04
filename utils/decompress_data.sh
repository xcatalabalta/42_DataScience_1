#!/bin/sh
# decompress_data.sh  (ex01)
# -----------------------------------------------------------------------------
# Completes the project setup by extracting the customer/ and item/ data folders
# from the subject archive into the REPOSITORY ROOT, so the exercise scripts
# (ex02, ex03, ex04) can read them via the relative paths customer/ and item/.
#
# This script lives in ex01/ and may be launched either from inside ex01/:
#       ./decompress_data.sh
# or from the repository root:
#       ./ex01/decompress_data.sh
# In both cases the data lands at the repository root, because the script
# locates itself and derives the root as its own parent directory.
#
# PREREQUISITE (see VM-instructions.txt): the subject archive must already be
# downloaded to ~/data_postgres/subject.zip. That archive and the extracted
# CSVs are NOT tracked in git (see .gitignore).
#
# The archive nests its content under a top-level subject/ directory:
#     subject/customer/data_2022_*.csv , data_2023_jan.csv
#     subject/item/item.csv
# This script strips that subject/ prefix so the folders land as data/customer/ and
# data/item/ at the repository root.
#
# Idempotent: safe to re-run. Skips work if the targets already exist and are
# populated (use --force to re-extract).
#
# The script has been made executable (chmod +x) so it can be run directly. 
# It is also safe to run via
#       sh ex01/decompress_data.sh
# -----------------------------------------------------------------------------
set -eu

ARCHIVE="${HOME}/data_postgres/subject.zip"
FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

# --- Precondition 1: operate from the repository root, wherever we were called
# The script lives in ex01/, one level below the repo root. Resolve both from
# the script's own location so the caller's current directory does not matter.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"   # absolute path to ex01/
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

# --- Skip if already populated (unless --force)
if [ "${FORCE}" -eq 0 ] && [ -d data/customer ] && [ -n "$(ls -A data/customer 2>/dev/null)" ] \
   && [ -d data/item ] && [ -n "$(ls -A data/item 2>/dev/null)" ]; then
    echo "data/customer/ and data/item/ already populated. Nothing to do."
    echo "(Re-run with --force to re-extract.)"
    exit 0
fi

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

rm -rf customer item data items
mkdir -p data
mv "${TMPDIR}/subject/customer" "data/customer"
mv "${TMPDIR}/subject/item" "data/items"
rm -rf "${TMPDIR}"

# --- Report
echo "Done."
echo "data/customer/ :"
ls -1 data/customer
echo "data/items/ :"
ls -1 data/items
