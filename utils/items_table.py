#!/usr/bin/env python3
"""
ex04 / items_table.py

Creates the PostgreSQL table `items` from item/item.csv.

Schema (subject requires at least THREE different data types):
  product_id     INTEGER       max ~5.9M (matches customer tables' product_id)
  category_id    BIGINT        19-digit values (~1.5e18); ~38% NULL
  category_code  VARCHAR(64)   ~99% NULL; longest observed value 38 chars
  brand          VARCHAR(64)   text brand names

Three distinct types: INTEGER, BIGINT, VARCHAR.

All columns are nullable by design: category_id (~38% empty) and category_code
(~99% empty) are frequently missing, and leaving every column nullable keeps
the load robust. Empty CSV fields are loaded as NULL (COPY ... NULL '').

Behaviour:
  - DROP TABLE IF EXISTS items, then CREATE, then bulk-load via
    COPY ... FROM STDIN (client-side, like \\copy).
  - Password requested once (getpass, no echo).
  - Self-locating: finds item/item.csv relative to this script (script lives in
    ex04/, data folder is a sibling of the exercise folders at the repo root).
  - Idempotent: safe to re-run.

Run it (both work; shebang present and file is executable):
    ./items_table.py
    python3 items_table.py
"""

import os
import sys
import getpass
import psycopg2

# --- Configuration ----------------------------------------------------------
DB_NAME = "piscineds"
DB_USER = "fcatala-"
DB_HOST = "localhost"
DB_PORT = 5432

TABLE_NAME = "items"
# folder (relative to repo root)
DATA_SUBDIR = "data/item"
CSV_FILENAME = "item.csv"

CREATE_SQL = f"""
CREATE TABLE {TABLE_NAME} (
    product_id      INTEGER,
    category_id     BIGINT,
    category_code   VARCHAR(64),
    brand           VARCHAR(64)
);
"""

COPY_SQL = f"""
COPY {TABLE_NAME} (product_id, category_id, category_code, brand)
FROM STDIN WITH (FORMAT csv, HEADER true, NULL '');
"""


def find_csv_path():
    """Resolve item/item.csv relative to the repo root, derived from this
    script's location (script lives in ex04/, repo root is its parent)."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)          # parent of ex04/
    csv_path = os.path.join(repo_root, DATA_SUBDIR, CSV_FILENAME)
    if not os.path.isfile(csv_path):
        sys.exit(f"ERROR: CSV not found at {csv_path}\n"
                 f"Did you run the decompress script first?")
    return csv_path


def get_password():
    env_pw = os.environ.get("PGPASSWORD")
    if env_pw:
        return env_pw
    return getpass.getpass(f"Password for PostgreSQL user '{DB_USER}': ")


def count_rows(file_path):
    """
    Count the number of rows in the CSV file, excluding the header.
    Arguments:
        file_path: path to the CSV file (string)
    Returns:
        the number of rows (int)
    """
    with open(file_path, "r") as f:
        lines = len(f.readlines())
    return lines - 1  # Exclude header


def main():
    csv_path = find_csv_path()
    password = get_password()

    try:
        conn = psycopg2.connect(
            dbname=DB_NAME, user=DB_USER, password=password,
            host=DB_HOST, port=DB_PORT,
        )
    except psycopg2.OperationalError as e:
        sys.exit(f"ERROR: could not connect to the database.\n{e}")

    try:
        # commits on success, rolls back on error
        with conn:
            with conn.cursor() as cur:
                print(f"Dropping table {TABLE_NAME} if it exists ...")
                cur.execute(f"DROP TABLE IF EXISTS {TABLE_NAME};")

                print(f"Creating table {TABLE_NAME} ...")
                cur.execute(CREATE_SQL)

                print(f"Loading data from {csv_path} ...")
                with open(csv_path, "r") as f:
                    cur.copy_expert(COPY_SQL, f)

                cur.execute(f"SELECT count(*) FROM {TABLE_NAME};")
                (rowcount,) = cur.fetchone()
                filerows = count_rows(csv_path)
                print(f"Done. {TABLE_NAME} now contains {rowcount} rows.")
                print(f"Original CSV file contains {filerows} rows of data.")
                print(f"{filerows - rowcount} row(s) skipped.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
