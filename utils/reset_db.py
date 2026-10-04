#!/usr/bin/env python3
"""
reset_db.py  --  "make fclean" for the piscineds database

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

DB_NAME = "piscineds"
DB_USER = "fcatala-"
DB_HOST = "localhost"
DB_PORT = 5432

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


def get_password():
    env_pw = os.environ.get("PGPASSWORD")
    if env_pw:
        return env_pw
    return getpass.getpass(f"Password for PostgreSQL user '{DB_USER}': ")


def main():
    skip_confirm = ("--yes" in sys.argv)

    password = get_password()
    try:
        conn = psycopg2.connect(
            dbname=DB_NAME, user=DB_USER, password=password,
            host=DB_HOST, port=DB_PORT,
        )
    except psycopg2.OperationalError as e:
        sys.exit(f"ERROR: could not connect to the database.\n{e}")

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
