#!/usr/bin/env python3
"""
ex01 / customers_table.py  (module 1 - Data Warehouse)

Joins all the monthly data_202*_*** tables into a single table "customers".

How it works:
  1. Lists the CSV files in ~/data_piscineds/data/customer (tilde expanded)
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

Run it (both work; shebang present and file is executable):
    ./customers_table.py
    python3 customers_table.py
"""

import os
import sys
import glob
import getpass
import psycopg2
from psycopg2 import sql

# --- Configuration ----------------------------------------------------------
DB_NAME = "piscineds"
DB_USER = "fcatala-"
DB_HOST = "localhost"
DB_PORT = 5432

TARGET_TABLE = "customers"
DATA_DIR = os.path.expanduser("~/data_piscineds/data/customer")
# Monthly table names: data_YYYY_mmm (e.g. data_2022_oct)
TABLE_REGEX = r"^data_20[0-9]{2}_[a-z]{3}$"


def get_password():
    """
    Get the database password without storing it in the code.
    Uses the PGPASSWORD environment variable if it is set, otherwise
    prompts the user without echoing what is typed.
    Arguments:
        none
    Returns:
        the password (str)
    """
    env_pw = os.environ.get("PGPASSWORD")
    if env_pw:
        return env_pw
    return getpass.getpass(f"Password for PostgreSQL user '{DB_USER}': ")


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
    Arguments:
        conn:   open psycopg2 connection
        tables: list of successfully appended table names (list of str)
    Returns:
        number of tables dropped (int); 0 if the user declines
    """
    print(f"\nThe following table(s) are now contained in {TARGET_TABLE}:")
    for t in tables:
        print(f"  - {t}")
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
    password = get_password()

    try:
        conn = psycopg2.connect(
            dbname=DB_NAME, user=DB_USER, password=password,
            host=DB_HOST, port=DB_PORT,
        )
    except psycopg2.OperationalError as e:
        sys.exit(f"ERROR: could not connect to the database.\n{e}")

    try:
        with conn:                   # build: one transaction, all or nothing
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
