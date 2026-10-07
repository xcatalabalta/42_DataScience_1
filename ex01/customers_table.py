#!/usr/bin/env python3
"""
ex01 / customers_table.py  (module 1 - Data Warehouse)

Joins all the monthly data_202*_*** tables into a single table "customers".

How it works: 1
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
  6. Verifies that the customers row count equals the sum of the monthly
     tables. Everything runs in ONE transaction: any error or a count
     mismatch rolls the whole build back.

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
    """Use PGPASSWORD if set, otherwise prompt without echo."""
    env_pw = os.environ.get("PGPASSWORD")
    if env_pw:
        return env_pw
    return getpass.getpass(f"Password for PostgreSQL user '{DB_USER}': ")


def expected_tables_from_csv():
    """Table names expected from the CSV files in DATA_DIR."""
    if not os.path.isdir(DATA_DIR):
        sys.exit(f"ERROR: data folder not found at {DATA_DIR}")
    csv_files = sorted(glob.glob(os.path.join(DATA_DIR, "*.csv")))
    if not csv_files:
        sys.exit(f"ERROR: no CSV files found in {DATA_DIR}")
    return [os.path.splitext(os.path.basename(p))[0] for p in csv_files]


def existing_monthly_tables(cur):
    """Monthly tables present in schema public, sorted by name."""
    cur.execute(
        "SELECT tablename FROM pg_tables "
        "WHERE schemaname = 'public' AND tablename ~ %s "
        "ORDER BY tablename;",
        (TABLE_REGEX,),
    )
    return [row[0] for row in cur.fetchall()]


def count_rows(cur, table):
    cur.execute(sql.SQL("SELECT count(*) FROM {};")
                .format(sql.Identifier(table)))
    return cur.fetchone()[0]


def build_customers(cur, tables):
    """Create customers with the schema of the first monthly table and
    append all monthly tables. Returns the sum of appended rows."""
    print(f"Dropping table {TARGET_TABLE} if it exists ...")
    cur.execute(sql.SQL("DROP TABLE IF EXISTS {};")
                .format(sql.Identifier(TARGET_TABLE)))

    print(f"Creating table {TARGET_TABLE} "
          f"(same structure as {tables[0]}) ...")
    cur.execute(sql.SQL("CREATE TABLE {} (LIKE {} INCLUDING ALL);")
                .format(sql.Identifier(TARGET_TABLE),
                        sql.Identifier(tables[0])))

    total = 0
    for table in tables:
        cur.execute(sql.SQL("INSERT INTO {} SELECT * FROM {};")
                    .format(sql.Identifier(TARGET_TABLE),
                            sql.Identifier(table)))
        print(f"  appended {table}: {cur.rowcount} rows")
        total += cur.rowcount
    return total


def main():
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
        with conn:                      # one transaction: all or nothing
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
    except (psycopg2.Error, RuntimeError) as e:
        sys.exit(f"ERROR: build rolled back.\n{e}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
