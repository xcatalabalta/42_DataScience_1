#!/usr/bin/env python3
"""
ex03 / fusion.py  (module 1 - Data Warehouse)

Combines the "customers" table with the "items" table: every customer event
gets the item columns (category_id, category_code, brand) of its product.
"Be careful not to lose any information" (subject) -> three facts about the
data drive the design (checked with utils/inspect_items.sql):

  1. items is NOT unique per product (109 579 rows, 54 043 products).
     A plain join would multiply customer rows, so items is first collapsed
     to ONE row per product.
  2. Repeated product rows do not only complement each other: some columns
     hold DIFFERENT non-NULL values (conflicts). Rule, per product and per
     column: keep the most frequent non-NULL value; ties -> the smallest
     (mode() WITHIN GROUP, which ignores NULLs, so a real value always wins
     over a gap). Every product with a conflict is stored in table
     "items_conflicts" with ALL its alternative values and the chosen one,
     so nothing is silently lost.
  3. Some customer events have no product in items (1 340). An INNER JOIN
     would drop them -> LEFT JOIN: they are kept with NULL item columns.

How it works (rebuild-and-swap, ONE transaction, TWO passes over customers):
  - pass 1: count customers AND the events without item in one query
  - temp table items_by_product (collapsed items) + table items_conflicts
  - CREATE customers_fused (LIKE customers) + the item columns, typed from
    the items table catalog (not hardcoded)
  - pass 2: INSERT ... SELECT customers LEFT JOIN items_by_product
  - checks: row count unchanged (no loss, no multiplication); the "after"
    count comes from the INSERT itself (no extra pass)
  - prints the duration of every step and the total (time waiting for
    the user is excluded from the processing total)
  - DROP customers, RENAME customers_fused -> customers, ANALYZE
  Idempotent: if customers already contains item columns (previous run),
  they are ignored and recomputed from items.

Options:
  --yes   skip the confirmation prompt

Run it (both work; shebang present and file is executable):
    ./fusion.py [--yes]
    python3 fusion.py [--yes]
"""

import os
import sys
import time
import getpass
from contextlib import contextmanager
import psycopg2
from psycopg2 import sql

# --- Configuration ----------------------------------------------------------
DB_NAME = "piscineds"
DB_USER = "fcatala-"
DB_HOST = "localhost"
DB_PORT = 5432

CUSTOMERS = "customers"
ITEMS = "items"
FUSED = "customers_fused"
COLLAPSED = "items_by_product"      # temporary table
CONFLICTS = "items_conflicts"       # kept as evidence
KEY = "product_id"
WORK_MEM = "256MB"


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


def get_columns_with_types(cur, table):
    """
    Read the columns of a table and their full SQL types (e.g. BIGINT,
    character varying(64)), in their defined order.
    Arguments:
        cur:   open psycopg2 cursor
        table: table name (str)
    Returns:
        list of (column_name, sql_type) tuples (list of tuple of str)
    """
    cur.execute(
        "SELECT attname, format_type(atttypid, atttypmod) "
        "FROM pg_attribute "
        "WHERE attrelid = %s::regclass AND attnum > 0 AND NOT attisdropped "
        "ORDER BY attnum;",
        (f"public.{table}",),
    )
    return cur.fetchall()


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


def collapse_items(cur, item_cols):
    """
    Build the temporary table COLLAPSED with ONE row per product: for each
    item column, the most frequent non-NULL value (ties -> smallest).
    Arguments:
        cur:       open psycopg2 cursor (inside the transaction)
        item_cols: item column names, without the key (list of str)
    Returns:
        number of products (rows of COLLAPSED) (int)
    """
    picks = sql.SQL(", ").join(
        sql.SQL("mode() WITHIN GROUP (ORDER BY {c}) AS {c}")
        .format(c=sql.Identifier(c)) for c in item_cols)
    cur.execute(sql.SQL("DROP TABLE IF EXISTS {};")
                .format(sql.Identifier(COLLAPSED)))
    cur.execute(sql.SQL(
        "CREATE TEMP TABLE {tmp} AS "
        "SELECT {key}, {picks} FROM {items} "
        "WHERE {key} IS NOT NULL GROUP BY {key};"
    ).format(tmp=sql.Identifier(COLLAPSED), key=sql.Identifier(KEY),
             picks=picks, items=sql.Identifier(ITEMS)))
    cur.execute(sql.SQL("CREATE UNIQUE INDEX ON {} ({});")
                .format(sql.Identifier(COLLAPSED), sql.Identifier(KEY)))
    cur.execute(sql.SQL("ANALYZE {};").format(sql.Identifier(COLLAPSED)))
    return count_rows(cur, COLLAPSED)


def save_conflicts(cur, item_cols):
    """
    (Re)create table CONFLICTS with one row per product that had more than
    one distinct non-NULL value in at least one item column. For every item
    column it stores all the alternatives (array) and the value chosen.
    Arguments:
        cur:       open psycopg2 cursor (inside the transaction)
        item_cols: item column names, without the key (list of str)
    Returns:
        dict {column: number of products with a conflict in it}
        (dict of str -> int)
    """
    alts = sql.SQL(", ").join(
        sql.SQL("array_agg(DISTINCT i.{c}) FILTER (WHERE i.{c} IS NOT NULL) "
                "AS {alts}, p.{c} AS {chosen}")
        .format(c=sql.Identifier(c), alts=sql.Identifier(f"{c}_values"),
                chosen=sql.Identifier(f"{c}_chosen"))
        for c in item_cols)
    any_conflict = sql.SQL(" OR ").join(
        sql.SQL("count(DISTINCT i.{c}) > 1").format(c=sql.Identifier(c))
        for c in item_cols)
    group = sql.SQL(", ").join(
        [sql.SQL("i.{}").format(sql.Identifier(KEY))]
        + [sql.SQL("p.{}").format(sql.Identifier(c)) for c in item_cols])
    cur.execute(sql.SQL("DROP TABLE IF EXISTS {};")
                .format(sql.Identifier(CONFLICTS)))
    cur.execute(sql.SQL(
        "CREATE TABLE {conf} AS "
        "SELECT i.{key}, count(*) AS item_rows, {alts} "
        "FROM {items} i JOIN {coll} p USING ({key}) "
        "GROUP BY {group} HAVING {any};"
    ).format(conf=sql.Identifier(CONFLICTS), key=sql.Identifier(KEY),
             alts=alts, items=sql.Identifier(ITEMS),
             coll=sql.Identifier(COLLAPSED), group=group, any=any_conflict))
    per_col = {}
    for c in item_cols:
        cur.execute(sql.SQL(
            "SELECT count(*) FROM {conf} WHERE cardinality({alts}) > 1;"
        ).format(conf=sql.Identifier(CONFLICTS),
                 alts=sql.Identifier(f"{c}_values")))
        per_col[c] = cur.fetchone()[0]
    return per_col


def build_fused(cur, base_cols, item_defs):
    """
    Create FUSED with the structure of CUSTOMERS (without any previous item
    columns) plus the item columns, and fill it with
    customers LEFT JOIN items_by_product.
    Arguments:
        cur:       open psycopg2 cursor (inside the transaction)
        base_cols: customers columns to keep (list of str)
        item_defs: item columns to add, as (name, sql_type) (list of tuple)
    Returns:
        number of rows inserted (int)
    """
    cur.execute(sql.SQL("DROP TABLE IF EXISTS {};")
                .format(sql.Identifier(FUSED)))
    cur.execute(sql.SQL("CREATE TABLE {} (LIKE {} INCLUDING ALL);")
                .format(sql.Identifier(FUSED), sql.Identifier(CUSTOMERS)))
    for name, _ in item_defs:           # previous run: drop stale item columns
        cur.execute(sql.SQL("ALTER TABLE {} DROP COLUMN IF EXISTS {};")
                    .format(sql.Identifier(FUSED), sql.Identifier(name)))
    for name, sql_type in item_defs:    # type read from the catalog
        cur.execute(sql.SQL("ALTER TABLE {} ADD COLUMN {} {};")
                    .format(sql.Identifier(FUSED), sql.Identifier(name),
                            sql.SQL(sql_type)))
    item_names = [n for n, _ in item_defs]
    target = sql.SQL(", ").join(map(sql.Identifier, base_cols + item_names))
    select = sql.SQL(", ").join(
        [sql.SQL("c.{}").format(sql.Identifier(c)) for c in base_cols]
        + [sql.SQL("p.{}").format(sql.Identifier(c)) for c in item_names])
    cur.execute(sql.SQL(
        "INSERT INTO {fused} ({target}) SELECT {select} "
        "FROM {cust} c LEFT JOIN {coll} p ON c.{key} = p.{key};"
    ).format(fused=sql.Identifier(FUSED), target=target, select=select,
             cust=sql.Identifier(CUSTOMERS), coll=sql.Identifier(COLLAPSED),
             key=sql.Identifier(KEY)))
    return cur.rowcount


def profile_customers(cur):
    """
    In ONE pass over CUSTOMERS, count its rows and the events whose product
    is not in ITEMS (they will be kept by the LEFT JOIN with NULL item
    columns). The join is made against the DISTINCT product ids of ITEMS,
    so it cannot multiply rows, and it runs as a hash join (no per-row
    subquery).
    Arguments:
        cur: open psycopg2 cursor
    Returns:
        tuple (total, unmatched) (tuple of int)
    """
    cur.execute(sql.SQL(
        "SELECT count(*), count(*) FILTER (WHERE p.{key} IS NULL) "
        "FROM {cust} c LEFT JOIN (SELECT DISTINCT {key} FROM {items}) p "
        "ON p.{key} = c.{key};"
    ).format(cust=sql.Identifier(CUSTOMERS), items=sql.Identifier(ITEMS),
             key=sql.Identifier(KEY)))
    total, unmatched = cur.fetchone()
    return total, unmatched


def swap_tables(cur):
    """
    Replace CUSTOMERS by FUSED and refresh the planner statistics.
    Arguments:
        cur: open psycopg2 cursor (inside the transaction)
    Returns:
        None
    """
    cur.execute(sql.SQL("DROP TABLE {};").format(sql.Identifier(CUSTOMERS)))
    cur.execute(sql.SQL("ALTER TABLE {} RENAME TO {};")
                .format(sql.Identifier(FUSED), sql.Identifier(CUSTOMERS)))
    cur.execute(sql.SQL("ANALYZE {};").format(sql.Identifier(CUSTOMERS)))


def confirm(rows):
    """
    Ask the user to confirm the fusion (skipped with --yes).
    Arguments:
        rows: current number of rows in CUSTOMERS (int)
    Returns:
        True if the user confirms, False otherwise (bool)
    """
    if "--yes" in sys.argv:
        return True
    answer = input(f"Fuse {ITEMS} into {CUSTOMERS} ({rows} rows)? [y/N] ")
    return answer.strip().lower() in ("y", "yes")


class Timer:
    """
    Measures the duration of each processing step and of the whole program.
    Time spent waiting for the user (password, confirmation) is NOT part
    of any step, so the processing total reflects the database work only.
    """

    def __init__(self):
        """
        Start the program clock.
        Arguments:
            none
        Returns:
            None
        """
        self.start = time.perf_counter()
        self.steps = []

    @contextmanager
    def step(self, label):
        """
        Context manager timing the block it wraps; the duration is recorded
        even if the block raises an exception.
        Arguments:
            label: name of the step shown in the report (str)
        Returns:
            a context manager (use: with timer.step("name"): ...)
        """
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.steps.append((label, time.perf_counter() - t0))

    def report(self):
        """
        Print the duration of every recorded step, the processing total
        (sum of steps) and the total wall-clock time of the program.
        Arguments:
            none
        Returns:
            None
        """
        print("\nTiming:")
        for label, secs in self.steps:
            print(f"  {label:<34}: {secs:9.2f} s")
        processing = sum(secs for _, secs in self.steps)
        print(f"  {'processing (database work)':<34}: {processing:9.2f} s")
        total = time.perf_counter() - self.start
        print(f"  {'total (incl. user input)':<34}: {total:9.2f} s")


def main():
    """
    Orchestrate the fusion: profile customers (one pass), collapse items,
    store conflicts, LEFT JOIN into a new table, check the row count, swap,
    commit, then print the timing of every step.
    Arguments:
        none (reads the --yes flag from sys.argv)
    Returns:
        None (exits with a non-zero status on error)
    """
    timer = Timer()
    password = get_password()
    try:
        with timer.step("connect"):
            conn = psycopg2.connect(
                dbname=DB_NAME, user=DB_USER, password=password,
                host=DB_HOST, port=DB_PORT,
            )
    except psycopg2.OperationalError as e:
        sys.exit(f"ERROR: could not connect to the database.\n{e}")

    try:
        cur = conn.cursor()             # one transaction: all or nothing
        for t in (CUSTOMERS, ITEMS):
            if not table_exists(cur, t):
                sys.exit(f"ERROR: table {t} not found.")
        cur.execute("SET LOCAL work_mem = %s;", (WORK_MEM,))

        item_defs = [(n, t) for n, t in
                     get_columns_with_types(cur, ITEMS) if n != KEY]
        item_names = [n for n, _ in item_defs]
        base_cols = [n for n, _ in get_columns_with_types(cur, CUSTOMERS)
                     if n not in item_names]
        if KEY not in base_cols:
            sys.exit(f"ERROR: {CUSTOMERS} has no {KEY} column.")

        with timer.step("profile customers (1 pass)"):
            before, orphans = profile_customers(cur)
        print(f"{CUSTOMERS}: {before} rows ({orphans} with a product "
              f"not in {ITEMS}) | {ITEMS}: {count_rows(cur, ITEMS)} rows")
        print(f"Item columns to add: {', '.join(item_names)}")
        if not confirm(before):
            conn.rollback()
            print("Aborted. No changes made.")
            return

        with timer.step("collapse items"):
            products = collapse_items(cur, item_names)
        print(f"items collapsed to {products} products "
              "(one row per product)")
        with timer.step("save conflicts"):
            per_col = save_conflicts(cur, item_names)
            n_conf = count_rows(cur, CONFLICTS)
        print(f"{CONFLICTS}: {n_conf} products with conflicting "
              "values (most frequent kept):")
        for c, n in per_col.items():
            print(f"  {c:<14}: {n}")

        with timer.step("build fused table (1 pass)"):
            fused = build_fused(cur, base_cols, item_defs)
        if fused != before:
            raise RuntimeError(f"row count changed: {before} -> {fused}")
        with timer.step("swap + analyze"):
            swap_tables(cur)
        with timer.step("commit"):
            conn.commit()

        print(f"Done. {CUSTOMERS}: {before} -> {fused} rows "
              "(no row lost, none multiplied)")
        print(f"  events matched in {ITEMS}  : {fused - orphans}")
        print(f"  events not in {ITEMS}      : {orphans} "
              "(kept, item columns NULL)")
    except (psycopg2.Error, RuntimeError) as e:
        conn.rollback()
        sys.exit(f"ERROR: operation rolled back.\n{e}")
    finally:
        conn.close()
        timer.report()


if __name__ == "__main__":
    main()
