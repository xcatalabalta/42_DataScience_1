#!/usr/bin/env python3
"""
ex01 / customers_table.py  (module 1 - Data Warehouse)

Joins all the monthly data_202*_*** tables into a single table "customers".

How it works:
  1. Lists the CSV files in the DATA_CUSTOMER folder of env_sample.txt / .env
     and derives the expected table names (data_2022_oct.csv -> data_2022_oct).
  2. Lists the monthly tables that actually exist in the database
     (pg_tables, schema public, name matching ^data_20[0-9]{2}_[a-z]{3}$).
  3. Cross-check: every CSV must have a loaded table, otherwise the script
     stops (customers would be incomplete). Tables without a CSV are
     reported and still included.
  4. DROP TABLE IF EXISTS customers, then
     CREATE TABLE customers (LIKE <first monthly table> INCLUDING ALL)
     so customers keeps the exact schema (types + NOT NULL constraints),
     which CREATE TABLE ... AS SELECT would not preserve.
  5. Appends every monthly table with INSERT ... SELECT (equivalent to
     UNION ALL: duplicates are KEPT on purpose, removing them is ex02).
     For each table, the rows appended are compared with the rows of the
     original table; any difference aborts the build.
  6. Verifies that the customers row count equals the sum of the monthly
     tables. Steps 4-6 run in ONE transaction: any error or count mismatch
     rolls the whole build back.
  7. Optional cleanse (asks for confirmation): once customers is committed,
     drops the monthly tables that were successfully appended, to free disk
     space. They can be rebuilt from the CSVs with module 0's scripts.

Options (for the cleanse of step 7):
  --keep   keep the monthly tables, do not ask (used by `make redo`)
  --yes    drop the monthly tables, do not ask

Run it (both work; shebang present and file is executable):
    ./customers_table.py [--keep | --yes]
    python3 customers_table.py [--keep | --yes]
"""

import os
import sys
import glob
import getpass
import psycopg2
from psycopg2 import sql


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

TARGET_TABLE = "customers"
DATA_DIR = os.path.expanduser(setting("DATA_CUSTOMER"))
# Monthly table names: data_YYYY_mmm (e.g. data_2022_oct)
TABLE_REGEX = r"^data_20[0-9]{2}_[a-z]{3}$"


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


def expected_tables_from_csv():
    """
    Derive the expected table names from the CSV files in DATA_DIR
    (data_2022_oct.csv -> data_2022_oct). Exits the program if the folder
    does not exist or contains no CSV file.
    Arguments:
        none (reads the module constant DATA_DIR)
    Returns:
        sorted list of expected table names (list of str)
    """
    if not os.path.isdir(DATA_DIR):
        sys.exit(f"ERROR: data folder not found at {DATA_DIR}")
    csv_files = sorted(glob.glob(os.path.join(DATA_DIR, "*.csv")))
    if not csv_files:
        sys.exit(f"ERROR: no CSV files found in {DATA_DIR}")
    return [os.path.splitext(os.path.basename(p))[0] for p in csv_files]


def existing_monthly_tables(cur):
    """
    List the monthly tables present in schema public whose name matches
    TABLE_REGEX (data_YYYY_mmm).
    Arguments:
        cur: open psycopg2 cursor
    Returns:
        sorted list of table names (list of str)
    """
    cur.execute(
        "SELECT tablename FROM pg_tables "
        "WHERE schemaname = 'public' AND tablename ~ %s "
        "ORDER BY tablename;",
        (TABLE_REGEX,),
    )
    return [row[0] for row in cur.fetchall()]


def count_rows(cur, table):
    """
    Count the rows of a table.
    Arguments:
        cur:   open psycopg2 cursor
        table: table name (str), quoted safely with sql.Identifier
    Returns:
        number of rows (int)
    """
    cur.execute(sql.SQL("SELECT count(*) FROM {};")
                .format(sql.Identifier(table)))
    return cur.fetchone()[0]


def append_table(cur, table):
    """
    Append one monthly table to TARGET_TABLE and compare the rows appended
    with the rows of the original table. A difference raises an error,
    which aborts (rolls back) the whole build.
    Arguments:
        cur:   open psycopg2 cursor (inside the build transaction)
        table: name of the monthly table to append (str)
    Returns:
        number of rows appended (int), equal to the original table's rows
    Raises:
        RuntimeError if appended rows != original rows
    """
    source_rows = count_rows(cur, table)
    cur.execute(sql.SQL("INSERT INTO {} SELECT * FROM {};")
                .format(sql.Identifier(TARGET_TABLE),
                        sql.Identifier(table)))
    appended = cur.rowcount
    if appended != source_rows:
        raise RuntimeError(
            f"{table}: appended {appended} rows but the original table "
            f"has {source_rows} rows")
    print(f"  {table:<15} original {source_rows:>10}  "
          f"appended {appended:>10}  OK")
    return appended


def build_customers(cur, tables):
    """
    Create TARGET_TABLE with the structure of the first monthly table
    (LIKE ... INCLUDING ALL keeps types and NOT NULL constraints) and
    append every monthly table, checking each one.
    Arguments:
        cur:    open psycopg2 cursor (inside the build transaction)
        tables: list of monthly table names to append (list of str)
    Returns:
        total number of rows appended (int)
    """
    print(f"Dropping table {TARGET_TABLE} if it exists ...")
    cur.execute(sql.SQL("DROP TABLE IF EXISTS {};")
                .format(sql.Identifier(TARGET_TABLE)))

    print(f"Creating table {TARGET_TABLE} "
          f"(same structure as {tables[0]}) ...")
    cur.execute(sql.SQL("CREATE TABLE {} (LIKE {} INCLUDING ALL);")
                .format(sql.Identifier(TARGET_TABLE),
                        sql.Identifier(tables[0])))

    print("Appending tables (original rows vs appended rows):")
    total = 0
    for table in tables:
        total += append_table(cur, table)
    return total


def cleanse(conn, tables):
    """
    Drop the monthly tables that were successfully appended to
    TARGET_TABLE, after asking the user for confirmation. Runs in its own
    transaction, only after the customers build has been committed.
    Flags: --keep skips the cleanse without asking (tables kept);
           --yes drops the tables without asking.
    Arguments:
        conn:   open psycopg2 connection
        tables: list of successfully appended table names (list of str)
    Returns:
        number of tables dropped (int); 0 if skipped or declined
    """
    if "--keep" in sys.argv:
        print("Cleanse skipped (--keep). Monthly tables kept.")
        return 0
    print(f"\nThe following table(s) are now contained in {TARGET_TABLE}:")
    for t in tables:
        print(f"  - {t}")
    if "--yes" not in sys.argv:
        answer = input("Drop them from the database to free space? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("Cleanse skipped. Monthly tables kept.")
            return 0

    with conn:                          # separate transaction
        with conn.cursor() as cur:
            for t in tables:
                cur.execute(sql.SQL("DROP TABLE IF EXISTS {};")
                            .format(sql.Identifier(t)))
                print(f"  dropped {t}")
    print(f"Cleanse done. {len(tables)} table(s) dropped.")
    return len(tables)


def main():
    """
    Orchestrate the whole process: cross-check CSVs vs tables, build
    customers in one transaction, verify counts, then offer the cleanse.
    Arguments:
        none
    Returns:
        None (exits with a non-zero status on error)
    """
    expected = expected_tables_from_csv()
    conn = connect_db()

    try:
        with conn:                      # build: one transaction, all or nothing
            with conn.cursor() as cur:
                tables = existing_monthly_tables(cur)

                missing = sorted(set(expected) - set(tables))
                extra = sorted(set(tables) - set(expected))
                if missing:
                    sys.exit("ERROR: CSV file(s) without a loaded table: "
                             f"{', '.join(missing)}\n"
                             "Load them first (module 0, automatic table).")
                if extra:
                    print("WARNING: table(s) without a CSV in "
                          f"{DATA_DIR} (included anyway): "
                          f"{', '.join(extra)}")

                print(f"Joining {len(tables)} table(s) into "
                      f"{TARGET_TABLE}: {', '.join(tables)}")
                appended = build_customers(cur, tables)

                final = count_rows(cur, TARGET_TABLE)
                if final != appended:
                    raise RuntimeError(
                        f"row count mismatch: {TARGET_TABLE} has {final} "
                        f"rows, expected {appended}")
                print(f"Done. {TARGET_TABLE} now contains {final} rows "
                      f"(sum of the monthly tables).")
        # Reaching here means the build was committed.
        cleanse(conn, tables)
    except (psycopg2.Error, RuntimeError) as e:
        sys.exit(f"ERROR: operation rolled back.\n{e}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
