#!/usr/bin/env python3
"""
reset_db.py  --  table reset used by `make clean` (and `make fclean`)

Drops ALL tables in the public schema of piscineds, returning the database
to an empty state.

What it RESETS:
  - every table in schema public (the four monthly tables, items, and anything
    else that accumulated)

What it PRESERVES (deliberately never touched):
  - the piscineds database itself
  - the fcatala- role and its password
  - pg_hba.conf and all authentication configuration
  - the public schema (only its tables are dropped, not the schema)

It is therefore a DATA reset, not a configuration reset: the VM/DB setup from
ex00 survives intact.

Run it:
    ./reset_db.py            # asks for confirmation first
    ./reset_db.py --yes      # skip the confirmation prompt
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

# Settings: .env (with password) or env_sample.txt -- see load_env()
DB_NAME = setting("PGDATABASE")
DB_USER = setting("PGUSER")
DB_HOST = setting("PGHOST")
DB_PORT = int(setting("PGPORT"))

# Drops every table in schema public, table-level only.
RESET_SQL = """
DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public') LOOP
        EXECUTE 'DROP TABLE IF EXISTS ' || quote_ident(r.tablename) ||
        ' CASCADE';
    END LOOP;
END $$;
"""


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


def main():
    skip_confirm = ("--yes" in sys.argv)

    conn = connect_db()

    try:
        with conn:
            with conn.cursor() as cur:
                # Show what will be dropped
                cur.execute(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname = 'public' ORDER BY tablename;"
                )
                tables = [row[0] for row in cur.fetchall()]

                if not tables:
                    print("No tables in public schema. Nothing to reset.")
                    return

                print("The following tables will be DROPPED from piscineds:")
                for t in tables:
                    print(f"  - {t}")

                if not skip_confirm:
                    ans = input("Proceed? [y/N] ").strip().lower()
                    if ans not in ("y", "yes"):
                        print("Aborted. No changes made.")
                        return

                cur.execute(RESET_SQL)
                print(f"Done. Dropped {len(tables)} table(s). "
                      f"Database, role and auth config are unchanged.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
