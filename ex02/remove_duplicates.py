#!/usr/bin/env python3
"""
ex02 / remove_duplicates.py  (module 1 - Data Warehouse)

Deletes the duplicate rows of the "customers" table, including the events
the server sends twice with a 1 second interval (subject warning).

Definition of a duplicate:
  Rows are grouped by EVERY column except event_time (event_type,
  product_id, price, user_id, user_session -- read from the catalog, not
  hardcoded) and ordered by event_time inside each group. A row is a
  duplicate when it arrives <= 1 second after the previous row of its
  group (LAG(event_time)):
    - exact duplicate      -> difference 0 s  -> removed
    - server re-send       -> difference 1 s  -> removed
    - same action later    -> difference > 1 s -> kept (genuine new event)
  Note: a chain of re-sends (0 s, 1 s, 2 s, ...) collapses to its first
  row, since each row is compared with the row just before it.

How it works (rebuild-and-swap, ONE transaction):
  1. CREATE TABLE customers_dedup (LIKE customers INCLUDING ALL)
  2. INSERT the rows that are NOT duplicates (window function LAG)
  3. DROP customers, RENAME customers_dedup -> customers, ANALYZE
  Rebuilding is much faster than DELETE on ~20M rows and leaves no dead
  rows (no VACUUM FULL needed). Any error rolls everything back, leaving
  the original customers table untouched.

Options:
  --yes     skip the confirmation prompt
  --check   also report how many duplicates are exact (0 s) vs re-sends
            (<= 1 s) before deleting, and verify afterwards that no
            duplicate remains (extra full scans: slower)

Run it (both work; shebang present and file is executable):
    ./remove_duplicates.py [--yes] [--check]
    python3 remove_duplicates.py [--yes] [--check]
"""

import os
import sys
import time
import getpass
import psycopg2
from psycopg2 import sql

# --- Configuration ----------------------------------------------------------
DB_NAME = "piscineds"
DB_USER = "fcatala-"
DB_HOST = "localhost"
DB_PORT = 5432

TABLE = "customers"
TMP_TABLE = "customers_dedup"
TIME_COL = "event_time"
MAX_GAP = "1 second"        # rows this close (or closer) are duplicates
WORK_MEM = "256MB"          # sort memory for the window function


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


def table_exists(cur, table):
    """
    Check whether a table exists in schema public.
    Arguments:
        cur:   open psycopg2 cursor
        table: table name (str)
    Returns:
        True if the table exists, False otherwise (bool)
    """
    cur.execute("SELECT to_regclass(%s) IS NOT NULL;", (f"public.{table}",))
    return cur.fetchone()[0]


def get_columns(cur, table):
    """
    Read the column names of a table, in their defined order.
    Arguments:
        cur:   open psycopg2 cursor
        table: table name (str)
    Returns:
        list of column names (list of str)
    """
    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s "
        "ORDER BY ordinal_position;",
        (table,),
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


def lagged_select(table, columns):
    """
    Build the inner query that adds, to every row, the event_time of the
    previous row of its group (same values in all the other columns).
    Arguments:
        table:   table name (str)
        columns: all column names of the table (list of str)
    Returns:
        composable SQL: SELECT <columns>, prev_time FROM <table> (sql.SQL)
    """
    group_cols = [c for c in columns if c != TIME_COL]
    return sql.SQL(
        "SELECT {cols}, LAG({t}) OVER "
        "(PARTITION BY {group} ORDER BY {t}) AS prev_time FROM {table}"
    ).format(
        cols=sql.SQL(", ").join(map(sql.Identifier, columns)),
        t=sql.Identifier(TIME_COL),
        group=sql.SQL(", ").join(map(sql.Identifier, group_cols)),
        table=sql.Identifier(table),
    )


def duplicate_stats(cur, table, columns):
    """
    Count the duplicates of a table without deleting anything, split into
    exact duplicates (same event_time) and re-sends (0 < gap <= MAX_GAP).
    Arguments:
        cur:     open psycopg2 cursor
        table:   table name (str)
        columns: all column names of the table (list of str)
    Returns:
        tuple (exact, resend) with the two counts (tuple of int)
    """
    query = sql.SQL(
        "SELECT "
        "count(*) FILTER (WHERE {t} = prev_time), "
        "count(*) FILTER (WHERE {t} > prev_time "
        "AND {t} - prev_time <= {gap}::interval) "
        "FROM ({inner}) AS s;"
    ).format(t=sql.Identifier(TIME_COL), gap=sql.Literal(MAX_GAP),
             inner=lagged_select(table, columns))
    cur.execute(query)
    exact, resend = cur.fetchone()
    return exact, resend


def build_dedup_table(cur, columns):
    """
    Create TMP_TABLE with the structure of TABLE and fill it with the rows
    that are not duplicates.
    Arguments:
        cur:     open psycopg2 cursor (inside the transaction)
        columns: all column names of TABLE (list of str)
    Returns:
        number of rows kept (int)
    """
    cur.execute(sql.SQL("DROP TABLE IF EXISTS {};")
                .format(sql.Identifier(TMP_TABLE)))
    cur.execute(sql.SQL("CREATE TABLE {} (LIKE {} INCLUDING ALL);")
                .format(sql.Identifier(TMP_TABLE), sql.Identifier(TABLE)))
    cols = sql.SQL(", ").join(map(sql.Identifier, columns))
    cur.execute(sql.SQL(
        "INSERT INTO {tmp} ({cols}) "
        "SELECT {cols} FROM ({inner}) AS s "
        "WHERE prev_time IS NULL OR {t} - prev_time > {gap}::interval;"
    ).format(tmp=sql.Identifier(TMP_TABLE), cols=cols,
             inner=lagged_select(TABLE, columns),
             t=sql.Identifier(TIME_COL), gap=sql.Literal(MAX_GAP)))
    return cur.rowcount


def swap_tables(cur):
    """
    Replace TABLE by TMP_TABLE (drop the old one, rename the new one) and
    refresh the planner statistics.
    Arguments:
        cur: open psycopg2 cursor (inside the transaction)
    Returns:
        None
    """
    cur.execute(sql.SQL("DROP TABLE {};").format(sql.Identifier(TABLE)))
    cur.execute(sql.SQL("ALTER TABLE {} RENAME TO {};")
                .format(sql.Identifier(TMP_TABLE), sql.Identifier(TABLE)))
    cur.execute(sql.SQL("ANALYZE {};").format(sql.Identifier(TABLE)))


def confirm(before):
    """
    Ask the user to confirm the deletion (skipped with --yes).
    Arguments:
        before: current number of rows in TABLE (int)
    Returns:
        True if the user confirms, False otherwise (bool)
    """
    if "--yes" in sys.argv:
        return True
    answer = input(f"Remove duplicates from {TABLE} ({before} rows)? [y/N] ")
    return answer.strip().lower() in ("y", "yes")


def main():
    """
    Orchestrate the process: optional statistics, confirmation,
    rebuild-and-swap in one transaction, report, optional verification.
    Arguments:
        none (reads the --yes and --check flags from sys.argv)
    Returns:
        None (exits with a non-zero status on error)
    """
    check = "--check" in sys.argv
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
                if not table_exists(cur, TABLE):
                    sys.exit(f"ERROR: table {TABLE} not found. "
                             "Run ex01 (customers_table) first.")
                cur.execute("SET LOCAL work_mem = %s;", (WORK_MEM,))
                columns = get_columns(cur, TABLE)
                if TIME_COL not in columns:
                    sys.exit(f"ERROR: {TABLE} has no {TIME_COL} column.")

                before = count_rows(cur, TABLE)
                print(f"{TABLE}: {before} rows")
                print(f"Duplicate = same {', '.join(c for c in columns if c != TIME_COL)}"
                      f" and {TIME_COL} <= {MAX_GAP} after the previous one")

                if check:
                    print("Counting duplicates (--check) ...")
                    exact, resend = duplicate_stats(cur, TABLE, columns)
                    print(f"  exact duplicates (0 s)       : {exact}")
                    print(f"  re-sends (<= {MAX_GAP})       : {resend}")

                if not confirm(before):
                    print("Aborted. No changes made.")
                    return

                start = time.time()
                print("Building de-duplicated table ...")
                kept = build_dedup_table(cur, columns)
                if kept > before:
                    raise RuntimeError(f"kept {kept} rows > original {before}")
                swap_tables(cur)
                removed = before - kept
                print(f"Done in {time.time() - start:.0f} s. "
                      f"{TABLE}: {before} -> {kept} rows "
                      f"({removed} duplicates removed, "
                      f"{100 * removed / before:.2f}%).")
                if check and removed != exact + resend:
                    raise RuntimeError(
                        f"removed {removed} rows but statistics "
                        f"predicted {exact + resend}")

        if check:
            with conn:
                with conn.cursor() as cur:
                    print("Verifying (--check) ...")
                    cur.execute("SET LOCAL work_mem = %s;", (WORK_MEM,))
                    exact, resend = duplicate_stats(cur, TABLE, columns)
                    if exact or resend:
                        raise RuntimeError(f"duplicates remain: {exact} "
                                           f"exact, {resend} re-sends")
                    print("  OK: no duplicate remains.")
    except (psycopg2.Error, RuntimeError) as e:
        sys.exit(f"ERROR: operation rolled back.\n{e}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
