#!/usr/bin/env python3
"""
utils / items_table.py

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
  - Password: uses the one stored by `make getpass` (.env) or
    PGPASSWORD; asks for it (no echo) only if neither works.
  - Fixed location according to new specifications
  - Idempotent: safe to re-run.

Run it (both work; shebang present and file is executable):
    ./items_table.py
    python3 items_table.py
"""

import os
import sys
import getpass
import psycopg2


def load_env():
    """
    Load the project settings into os.environ, from two files at the
    repository root (one level above this script):
      1. .env        created by `make getpass`, includes the password
      2. env_sample.txt  committed defaults (everything except the password)
    A variable already set is never overridden, so the priority is:
    shell / make environment > .env > env_sample.txt.
    Arguments:
        none
    Returns:
        list of the files that were loaded (list of str)
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    loaded = []
    for name in (".env", "env_sample.txt"):
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            continue
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())
        loaded.append(path)
    return loaded


def setting(name):
    """
    Read one project setting from the environment (filled by load_env()).
    Arguments:
        name: variable name, e.g. "PGDATABASE" (str)
    Returns:
        its value (str); exits with a clear message if it is missing
    """
    value = os.environ.get(name)
    if not value:
        sys.exit(f"ERROR: setting {name} not found (check env_sample.txt / .env).")
    return value


load_env()

# --- Configuration ----------------------------------------------------------
# Settings: .env (with password) or env_sample.txt -- see load_env()
DB_NAME = setting("PGDATABASE")
DB_USER = setting("PGUSER")
DB_HOST = setting("PGHOST")
DB_PORT = int(setting("PGPORT"))

TABLE_NAME = "items"
DATA_SUBDIR = setting("DATA_ITEMS")           # folder with item.csv
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
    """
    Resolve items/item.csv expanding user
    """
    # script_dir = os.path.dirname(os.path.abspath(__file__))
    # repo_root = os.path.dirname(script_dir)
    csv_path = os.path.expanduser(os.path.join(DATA_SUBDIR, CSV_FILENAME))
    if not os.path.isfile(csv_path):
        sys.exit(f"ERROR: CSV not found at {csv_path}\n"
                 f"Did you run the decompress script first?")
    return csv_path


def connect_db():
    """
    Open a connection to the database without storing any password in the
    code. It first tries WITHOUT a password: libpq then uses, if present,
    PGPASSWORD, loaded from the project's .env (written by
    `make getpass`) or exported by the shell. Only if that first attempt fails does it prompt for
    the password (no echo) and try once more.
    Arguments:
        none (reads DB_NAME, DB_USER, DB_HOST and DB_PORT)
    Returns:
        an open psycopg2 connection
    Exits:
        with an error message if the second attempt also fails
    """
    params = dict(dbname=DB_NAME, user=DB_USER, host=DB_HOST, port=DB_PORT)
    try:
        return psycopg2.connect(**params)
    except psycopg2.OperationalError:
        pass                            # no stored password, or a wrong one
    password = getpass.getpass(f"Password for PostgreSQL user '{DB_USER}': ")
    try:
        return psycopg2.connect(password=password, **params)
    except psycopg2.OperationalError as e:
        sys.exit(f"ERROR: could not connect to the database.\n{e}")


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
    conn = connect_db()

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
