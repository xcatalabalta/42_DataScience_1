#!/usr/bin/env python3
"""
utils / automatic_table.py

Automatically creates a PostgreSQL table for every CSV in the customer/ folder,
each table named after its CSV file (without the .csv extension), e.g.
data_2022_oct.csv -> table data_2022_oct.

All customer CSVs share the same structure, so the same schema is applied to
each (DATETIME first column + six distinct data types):
  event_time    TIMESTAMP      datetime, first column
  event_type    VARCHAR(32)    short label
  product_id    INTEGER
  price         NUMERIC(10,2)
  user_id       BIGINT
  user_session  UUID

Behaviour:
  - Reads every *.csv in customer.
  - For each file: DROP TABLE IF EXISTS, then CREATE, then bulk-load via
    COPY ... FROM STDIN (client-side, like \\copy).
  - Per-file transaction: a failure on one file is reported to the console and
    the loop continues with the next file; already-loaded tables are kept.
  - Password: uses the one stored by `make getpass` (.env) or
    PGPASSWORD; asks for it (no echo) only if neither works.
    One connection is reused for all files.
  - Idempotent: safe to re-run (each table is dropped and recreated).

Run it (both work; shebang present and file is executable):
    ./automatic_table.py
    python3 automatic_table.py
"""

import os
import sys
import glob
import getpass
import psycopg2


def load_env():
    """
    Load the project settings into os.environ, from two files at the
    repository root (one level above this script):
      1. .env        created by `make getpass`, includes the password
      2. env_sample.txt  committed defaults (everything except the password)
    A variable already set to a non-empty value is never overridden, so
    the priority is: shell / make environment > .env > env_sample.txt.
    An EMPTY variable counts as unset: make exports the empty PGPASSWORD
    of env_sample.txt when .env does not exist yet, and that must not hide
    the password that `make getpass` has just written to .env.
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
                key, value = key.strip(), value.strip()
                if value and not os.environ.get(key):   # empty = unset
                    os.environ[key] = value
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
DATA_SUBDIR = setting("DATA_CUSTOMER")        # folder with the CSVs


def create_sql(table):
    return f"""
CREATE TABLE {table} (
    event_time      TIMESTAMP     NOT NULL,
    event_type      VARCHAR(32)   NOT NULL,
    product_id      INTEGER,
    price           NUMERIC(10,2),
    user_id         BIGINT,
    user_session    UUID
);
"""


def copy_sql(table):
    return f"""
COPY {table} (event_time, event_type, product_id, price, user_id, user_session)
FROM STDIN WITH (FORMAT csv, HEADER true, NULL '');
"""


def find_csv_path():
    """
    Resolve the customer/ folder.
    Arguments:
        None
    Returns:
        the absolute path to the customer/ folder (string) if found,
    or exits with an error if not found.
    """
    # script_dir = os.path.dirname(os.path.abspath(__file__))
    # repo_root = os.path.dirname(script_dir)
    csv_path = os.path.expanduser(os.path.join(DATA_SUBDIR))
    if not os.path.isdir(csv_path):
        sys.exit(f"ERROR: data folder not found at {csv_path}\n"
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
        # Skip the header line
        next(f)
        return sum(1 for _ in f)


def table_name_from_path(csv_path):
    """
    Derive a table name from the CSV file name, e.g.
    data_2022_oct.csv -> data_2022_oct
    Arguments:
        csv_path: absolute or relative path to a CSV file (string)
    Returns:
        the table name derived from the CSV file name (string)
    """
    base = os.path.basename(csv_path)          # strip directory
    return os.path.splitext(base)[0]           # strip .csv extension


def load_one(conn, csv_path):
    """
    Create and load a single table. Uses its own transaction so a failure
    here does not roll back tables already loaded in previous iterations.
    Arguments:
        conn: psycopg2 connection object
        csv_path: absolute or relative path to a CSV file (string)
    Returns:
        table: the table name created (string)
        rowcount: number of rows loaded (int)
    Raises:
        psycopg2.Error: if any SQL operation fails (table creation or COPY)
    """
    table = table_name_from_path(csv_path)
    with conn:                                 # per-file transaction
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS {table};")
            cur.execute(create_sql(table))
            with open(csv_path, "r") as f:
                cur.copy_expert(copy_sql(table), f)
            cur.execute(f"SELECT count(*) FROM {table};")
            (rowcount,) = cur.fetchone()
    return table, rowcount


def main():
    data_dir = find_csv_path()
    csv_files = sorted(glob.glob(os.path.join(data_dir, "*.csv")))
    if not csv_files:
        sys.exit(f"ERROR: no CSV files found in {data_dir}")

    print(f"Found {len(csv_files)} CSV file(s) in {data_dir}:")
    for p in csv_files:
        print(f"  - {os.path.basename(p)}")

    conn = connect_db()

    ok, failed = 0, 0
    try:
        for csv_path in csv_files:
            name = os.path.basename(csv_path)
            try:
                print(f"\nProcessing {name} ...")
                table, rowcount = load_one(conn, csv_path)
                print(f"  OK: table {table} loaded with {rowcount} rows.")
                filerows = count_rows(csv_path)
                print(f"  Original file contains {filerows} rows of data.")
                print(f"  {filerows - rowcount} row(s) skipped.")
                ok += 1
            except Exception as e:                     # report and continue
                # The failed transaction was rolled back by 'with conn:'.
                print(f"  ERROR loading {name}: {e}", file=sys.stderr)
                failed += 1
    finally:
        conn.close()

    print(f"\nDone. {ok} table(s) loaded, {failed} failed.")
    if failed:
        sys.exit(1)          # non-zero exit if anything failed


if __name__ == "__main__":
    main()
