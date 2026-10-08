#!/usr/bin/env python3
"""
ex02 / remove_duplicates.py  (module 1 - Data Warehouse)

Deletes the duplicate rows of the "customers" table, including the events
the server sends twice with a 1 second interval (subject warning), and
keeps the removed rows in a separate table "dups_customer" for inspection.

Definition of a duplicate:
  Rows are grouped by EVERY column except event_time (event_type,
  product_id, price, user_id, user_session -- read from the catalog, not
  hardcoded) and ordered by event_time inside each group. A row is a
  duplicate when it arrives <= 1 second after the previous row of its
  group (LAG(event_time)):
    - exact duplicate      -> difference 0 s  -> removed ('exact')
    - server re-send       -> difference 1 s  -> removed ('resend')
    - same action later    -> difference > 1 s -> kept (genuine new event)
  Note: a chain of re-sends (0 s, 1 s, 2 s, ...) collapses to its first
  row, since each row is compared with the row just before it.

How it works (ONE transaction, ONE pass over customers):
  1. CREATE customers_dedup (LIKE customers) and dups_customer_new
     (LIKE customers + prev_event_time + dup_kind).
  2. A single statement computes LAG once (materialized CTE) and, thanks
     to a data-modifying CTE, sends each row to one of the two tables:
     kept rows -> customers_dedup, duplicates -> dups_customer_new.
  3. Conservation check: kept + duplicates must equal the original count.
  4. DROP customers, RENAME customers_dedup -> customers, ANALYZE.
     dups_customer_new replaces dups_customer only if duplicates were
     found (a re-run on clean data does not wipe the previous evidence).
  Any error rolls everything back, leaving customers untouched.

Table dups_customer:
  same columns as customers, plus
    prev_event_time  event_time of the row it duplicates
    dup_kind         'exact' (0 s) or 'resend' (<= 1 s)

Options:
  --yes     skip the confirmation prompt
  --check   count duplicates BEFORE asking for confirmation (extra full
            scan) and verify AFTERWARDS that none remains (another scan)

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
DUPS_TABLE = "dups_customer"
DUPS_TMP = "dups_customer_new"
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


def lagg_select(table, columns):
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
             inner=lagg_select(table, columns))
    cur.execute(query)
    exact, resend = cur.fetchone()
    return exact, resend


def create_target_tables(cur):
    """
    Create the two empty target tables: TMP_TABLE (kept rows, same
    structure as TABLE) and DUPS_TMP (duplicates, same structure plus
    prev_event_time and dup_kind).
    Arguments:
        cur: open psycopg2 cursor (inside the transaction)
    Returns:
        None
    """
    for name in (TMP_TABLE, DUPS_TMP):
        cur.execute(sql.SQL("DROP TABLE IF EXISTS {};")
                    .format(sql.Identifier(name)))
        cur.execute(sql.SQL("CREATE TABLE {} (LIKE {} INCLUDING ALL);")
                    .format(sql.Identifier(name), sql.Identifier(TABLE)))
    cur.execute(sql.SQL(
        "ALTER TABLE {} ADD COLUMN prev_event_time TIMESTAMP, "
        "ADD COLUMN dup_kind VARCHAR(8);"
    ).format(sql.Identifier(DUPS_TMP)))


def split_rows(cur, columns):
    """
    In a single pass over TABLE, send the kept rows to TMP_TABLE and the
    duplicates to DUPS_TMP. LAG is computed once (materialized CTE); the
    duplicates are written by a data-modifying CTE.
    Arguments:
        cur:     open psycopg2 cursor (inside the transaction)
        columns: all column names of TABLE (list of str)
    Returns:
        tuple (kept, dups): rows written to each table (tuple of int)
    """
    cols = sql.SQL(", ").join(map(sql.Identifier, columns))
    t = sql.Identifier(TIME_COL)
    gap = sql.Literal(MAX_GAP)
    cur.execute(sql.SQL(
        "WITH s AS MATERIALIZED ({inner}), "
        "d AS ("
        "  INSERT INTO {dups} ({cols}, prev_event_time, dup_kind) "
        "  SELECT {cols}, prev_time, "
        "         CASE WHEN {t} = prev_time THEN 'exact' ELSE 'resend' END "
        "  FROM s "
        "  WHERE prev_time IS NOT NULL AND {t} - prev_time <= {gap}::interval"
        ") "
        "INSERT INTO {kept} ({cols}) "
        "SELECT {cols} FROM s "
        "WHERE prev_time IS NULL OR {t} - prev_time > {gap}::interval;"
    ).format(inner=lagg_select(TABLE, columns), dups=sql.Identifier(DUPS_TMP),
             kept=sql.Identifier(TMP_TABLE), cols=cols, t=t, gap=gap))
    kept = cur.rowcount
    return kept, count_rows(cur, DUPS_TMP)


def dups_breakdown(cur, table):
    """
    Count the stored duplicates by kind ('exact' / 'resend').
    Arguments:
        cur:   open psycopg2 cursor
        table: duplicates table name (str)
    Returns:
        dict {dup_kind: count} (dict of str -> int)
    """
    cur.execute(sql.SQL("SELECT dup_kind, count(*) FROM {} GROUP BY dup_kind;")
                .format(sql.Identifier(table)))
    return dict(cur.fetchall())


def swap_tables(cur, dups_found):
    """
    Replace TABLE by TMP_TABLE and refresh its statistics. Replace
    DUPS_TABLE by DUPS_TMP only if duplicates were found; otherwise drop
    DUPS_TMP so a re-run on clean data keeps the previous evidence.
    Arguments:
        cur:        open psycopg2 cursor (inside the transaction)
        dups_found: number of duplicates found in this run (int)
    Returns:
        None
    """
    cur.execute(sql.SQL("DROP TABLE {};").format(sql.Identifier(TABLE)))
    cur.execute(sql.SQL("ALTER TABLE {} RENAME TO {};")
                .format(sql.Identifier(TMP_TABLE), sql.Identifier(TABLE)))
    cur.execute(sql.SQL("ANALYZE {};").format(sql.Identifier(TABLE)))
    if dups_found:
        cur.execute(sql.SQL("DROP TABLE IF EXISTS {};")
                    .format(sql.Identifier(DUPS_TABLE)))
        cur.execute(sql.SQL("ALTER TABLE {} RENAME TO {};")
                    .format(sql.Identifier(DUPS_TMP),
                            sql.Identifier(DUPS_TABLE)))
    else:
        cur.execute(sql.SQL("DROP TABLE {};").format(sql.Identifier(DUPS_TMP)))


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
    answer = input(f"Remove duplicates from {TABLE} ({before} rows) and "
                   f"store them in {DUPS_TABLE}? [y/N] ")
    return answer.strip().lower() in ("y", "yes")


def main():
    """
    Orchestrate the process: optional statistics, confirmation, single-pass
    split into kept rows and duplicates, conservation check, swap, report,
    optional verification.
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
                group = ", ".join(c for c in columns if c != TIME_COL)
                print(f"{TABLE}: {before} rows")
                print(f"Duplicate = same {group} and {TIME_COL} "
                      f"<= {MAX_GAP} after the previous one")

                if check:
                    print("Counting duplicates (--check) ...")
                    exact, resend = duplicate_stats(cur, TABLE, columns)
                    print(f"  exact duplicates (0 s)       : {exact}")
                    print(f"  re-sends (<= {MAX_GAP})       : {resend}")

                if not confirm(before):
                    print("Aborted. No changes made.")
                    return

                start = time.time()
                print("Splitting rows into kept rows and duplicates ...")
                create_target_tables(cur)
                kept, dups = split_rows(cur, columns)
                if kept + dups != before:
                    raise RuntimeError(
                        f"conservation check failed: kept {kept} + "
                        f"duplicates {dups} != original {before}")
                breakdown = dups_breakdown(cur, DUPS_TMP)
                swap_tables(cur, dups)

                print(f"Done in {time.time() - start:.0f} s.")
                print(f"  {TABLE:<14}: {before} -> {kept} rows")
                print(f"  {DUPS_TABLE:<14}: {dups} rows "
                      f"({100 * dups / before:.2f}%) "
                      f"exact={breakdown.get('exact', 0)} "
                      f"resend={breakdown.get('resend', 0)}"
                      + ("" if dups else "  (no new duplicates: previous "
                         f"{DUPS_TABLE} kept)"))
                print(f"  conservation  : {kept} + {dups} = {before} OK")
                if check and dups != exact + resend:
                    raise RuntimeError(
                        f"removed {dups} rows but statistics "
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
