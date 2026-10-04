#!/bin/sh
# inspect_customer.sh  (utils)
# -----------------------------------------------------------------------------
# Verifies that all CSV files in data/customer/ share the SAME structure, so the
# single schema used by ex02/ex03 is justified. Consistency check for defense:
# instead of "I checked each file by hand", it proves it.
#
# The reference structure is taken from the FIRST file (alphabetically), not
# hardcoded:
#   - EXPECTED_HEADER  = that file's header line
#   - EXPECTED_FIELDS  = number of columns in that header
# Every other file is then checked to match. (Assumption: the first file is
# itself correct, verified during manual inspection.)
#
# Checks performed per file:
#   1. Header identical to the reference file's header.
#   2. Every data row has exactly EXPECTED_FIELDS fields.
#   3. Row count reported (evidence / load-verification numbers).
#   4. Empty-value count per column (how many rows have that field blank) ->
#      informs nullability decisions and the COPY ... NULL '' handling.
#   5. Type-shape sanity (only for the known 6-column customer layout:
#      event_time,event_type,product_id,price,user_id,user_session):
#        product_id (col3) int, price (col4) numeric, user_id (col5) int,
#        user_session (col6) UUID or empty.
#      Column semantics cannot be auto-derived, so this runs only for 6 columns.
#
# Exits 0 if consistent, non-zero if any problem is found.
# Self-locating: finds data/customer relative to the repo root (script in utils/).
#
# Run:  ./utils/inspect_customer.sh   |   sh utils/inspect_customer.sh
# -----------------------------------------------------------------------------
set -eu

# --- Locate data/customer relative to the repo root -------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"   # utils/
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"   # repo root
DATA_DIR="${REPO_ROOT}/data/customer"

if [ ! -d "${DATA_DIR}" ]; then
    echo "ERROR: ${DATA_DIR} not found. Run the decompress step first." >&2
    exit 1
fi

# Collect the CSV files (sorted, stable order)
CSV_FILES="$(find "${DATA_DIR}" -maxdepth 1 -name '*.csv' | sort)"
if [ -z "${CSV_FILES}" ]; then
    echo "ERROR: no CSV files in ${DATA_DIR}" >&2
    exit 1
fi

# --- Derive the reference structure from the FIRST file ---------------------
FIRST_FILE="$(printf '%s\n' ${CSV_FILES} | head -1)"
EXPECTED_HEADER="$(head -1 "${FIRST_FILE}")"
EXPECTED_FIELDS="$(printf '%s' "${EXPECTED_HEADER}" | awk -F',' '{print NF}')"

echo "Inspecting CSV files in ${DATA_DIR}"
echo "Reference file  : $(basename "${FIRST_FILE}")"
echo "Expected header : ${EXPECTED_HEADER}"
echo "Expected fields : ${EXPECTED_FIELDS}"
echo "-------------------------------------------------------------------------"

problems=0

for f in ${CSV_FILES}; do
    name="$(basename "${f}")"
    echo ""
    echo "== ${name} =="

    # --- Check 1: header matches the reference
    header="$(head -1 "${f}")"
    if [ "${header}" = "${EXPECTED_HEADER}" ]; then
        echo "  header      : OK"
    else
        echo "  header      : MISMATCH"
        echo "                got: ${header}"
        problems=$((problems + 1))
    fi

    # --- Check 2: every data row has exactly EXPECTED_FIELDS fields
    bad_fields="$(tail -n +2 "${f}" | awk -F',' -v n="${EXPECTED_FIELDS}" \
        'NF!=n{c++} END{print c+0}')"
    if [ "${bad_fields}" -eq 0 ]; then
        echo "  field count : OK (all rows have ${EXPECTED_FIELDS} fields)"
    else
        echo "  field count : ${bad_fields} row(s) NOT ${EXPECTED_FIELDS} fields"
        problems=$((problems + 1))
    fi

    # --- Check 3: row count (data rows, excluding header)
    rows="$(tail -n +2 "${f}" | wc -l)"
    echo "  data rows   : ${rows}"

    # --- Check 4: empty-value count per column (generic over EXPECTED_FIELDS)
    #   For each field position 1..EXPECTED_FIELDS, count rows where it is "".
    echo "  empty per column:"
    tail -n +2 "${f}" | awk -F',' -v n="${EXPECTED_FIELDS}" -v hdr="${EXPECTED_HEADER}" '
        BEGIN { split(hdr, names, ",") }
        {
            for (i = 1; i <= n; i++)
                if ($i == "") empty[i]++
        }
        END {
            for (i = 1; i <= n; i++)
                printf "                %-14s : %d\n", names[i], empty[i]+0
        }'

    # --- Check 5: unique values in column 2 (event_type)
    echo "  content of column 2 (event_type) :"
    tail -n +2 "${f}" | cut -d',' -f2 | sort -u | awk '{print "                " $0}'

    # --- Checks 6 combined: type-shape sanity + min/max + int-range,
    #     in a single pass (only for the known 6-column customer layout)
    if [ "${EXPECTED_FIELDS}" -eq 6 ]; then
        INT32_MAX=2147483647
        result="$(tail -n +2 "${f}" | awk -F',' -v imax="${INT32_MAX}" '
            # shape checks (only on non-empty values)
            $3 != "" && $3 !~ /^[0-9]+$/ { badp++ }
            $4 != "" && $4 !~ /^-?[0-9]+(\.[0-9]+)?$/ { badpr++ }
            $5 != "" && $5 !~ /^[0-9]+$/ { badu++ }
            $6 != "" && $6 !~ /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/ { bads++ }
            # min/max for the three numeric columns (only on non-empty values)
            $3 != "" { if (minp=="" || $3<minp) minp=$3; if (maxp=="" || $3>maxp) maxp=$3 }
            $4 != "" { if (minpr=="" || $4<minpr) minpr=$4; if (maxpr=="" || $4>maxpr) maxpr=$4 }
            $5 != "" { if (minu=="" || $5<minu) minu=$5; if (maxu=="" || $5>maxu) maxu=$5 }
            END {
                # int-range verdicts computed in awk (no shell integer limits)
                p_over = (maxp != "" && maxp > imax) ? 1 : 0
                u_over = (maxu != "" && (maxu > imax || minu < 0)) ? 1 : 0
                # emit: badp badpr badu bads  minp maxp minpr maxpr minu maxu  p_over u_over
                printf "%d %d %d %d %s %s %s %s %s %s %d %d",
                    badp+0, badpr+0, badu+0, bads+0,
                    (minp==""?"NA":minp), (maxp==""?"NA":maxp),
                    (minpr==""?"NA":minpr), (maxpr==""?"NA":maxpr),
                    (minu==""?"NA":minu), (maxu==""?"NA":maxu),
                    p_over, u_over
            }')"
        set -- ${result}
        badp=$1; badpr=$2; badu=$3; bads=$4
        minp=$5; maxp=$6; minpr=$7; maxpr=$8; minu=$9; maxu=${10}
        p_over=${11}; u_over=${12}

        # report shapes
        if [ "${badp}" -eq 0 ] && [ "${badpr}" -eq 0 ] \
           && [ "${badu}" -eq 0 ] && [ "${bads}" -eq 0 ]; then
            echo "  type shapes : OK (product_id/user_id int, price numeric, session UUID)"
        else
            echo "  type shapes : product_id bad=${badp}, price bad=${badpr}, user_id bad=${badu}, session bad=${bads}"
            problems=$((problems + 1))
        fi

        # report min/max
        echo "  min/max     : product_id [${minp}, ${maxp}], price [${minpr}, ${maxpr}], user_id [${minu}, ${maxu}]"

        # report int-range verdicts (computed in awk, safe for large numbers)
        if [ "${p_over}" -eq 1 ]; then
            echo "                WARNING: product_id exceeds 32-bit integer range (needs BIGINT)."
        else
            echo "                product_id within 32-bit integer range."
        fi
        if [ "${u_over}" -eq 1 ]; then
            echo "                WARNING: user_id exceeds 32-bit integer range (needs BIGINT)."
        else
            echo "                user_id within 32-bit integer range."
        fi
    else
        echo "  type shapes : skipped (only checked for the known 6-column shape)"
        echo "  min/max     : skipped (only checked for the known 6-column shape)"
    fi
done

echo ""
echo "-------------------------------------------------------------------------"
if [ "${problems}" -eq 0 ]; then
    echo "RESULT: all files are consistent (header + field count$( [ "${EXPECTED_FIELDS}" -eq 6 ] && printf ' + valid shapes')). Empty counts reported above."
    exit 0
else
    echo "RESULT: ${problems} problem(s) found. See details above."
    exit 1
fi
