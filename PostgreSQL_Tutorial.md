# PostgreSQL Tutorial

A learning guide to the PostgreSQL commands used in the 42 *Piscine Data Science*
(modules 0 and 1: creating a database, loading CSV files, and building a data warehouse
table).

Every entry follows the same pattern: **syntax**, **what it does**, **expected result**.
The examples use this project's names (database `piscineds`, user `fcatala-`, tables
`customers` and `items`). Replace them with your own.

> System-specific details (package names, service commands, file locations) are given
> for **Alpine Linux** with PostgreSQL 17. On other systems the SQL is the same; only
> the installation and service commands change.

## Contents

1. [Installing and running the server](#1-installing-and-running-the-server)
2. [Connecting with psql](#2-connecting-with-psql)
3. [Roles and databases](#3-roles-and-databases)
4. [Authentication](#4-authentication)
5. [Data types](#5-data-types)
6. [Creating tables and loading CSV files](#6-creating-tables-and-loading-csv-files)
7. [Inspecting data and the catalog](#7-inspecting-data-and-the-catalog)
8. [Building tables from other tables](#8-building-tables-from-other-tables)
9. [Window functions](#9-window-functions)
10. [Aggregate functions](#10-aggregate-functions)
11. [Common table expressions (WITH)](#11-common-table-expressions-with)
12. [Joins](#12-joins)
13. [Transactions and session settings](#13-transactions-and-session-settings)
14. [Cleaning up](#14-cleaning-up)
15. [Using PostgreSQL from Python (psycopg2)](#15-using-postgresql-from-python-psycopg2)
16. [Common pitfalls](#16-common-pitfalls)

---

## 1. Installing and running the server

### Install

```sh
apk search -x postgresql | sort          # list the versions your Alpine release offers
sudo apk add postgresql17 postgresql17-contrib
```

Installs the server, the `psql` client and the extra modules. Each Alpine release ships
a fixed set of major versions, so pick the newest one it offers.

### Start the service (OpenRC)

```sh
sudo rc-update add postgresql default    # start automatically at boot
sudo rc-service postgresql start         # start now
sudo rc-service postgresql status        # expect: status: started
sudo rc-service postgresql reload        # re-read the configuration files
```

On Alpine, the first `start` also **initialises the cluster** (it runs `initdb`) if the
data folder is empty, so no manual `initdb` is needed.

### Where things live (Alpine)

| What | Path |
|---|---|
| Data (the cluster) | `/var/lib/postgresql/17/data` |
| Configuration (`postgresql.conf`, `pg_hba.conf`) | `/etc/postgresql/` |
| Local socket | `/run/postgresql` |

The configuration is **outside** the data folder on Alpine. Section 4 shows how to ask
the server which file it actually uses.

---

## 2. Connecting with psql

### As the superuser

```sh
sudo su postgres -c psql
```

Runs `psql` as the system user `postgres` (the database superuser), through the local
socket. Result: a `postgres=#` prompt. `sudo` is needed because switching to the
`postgres` account without it asks for that account's password, which doesn't exist.

Note that `sudo postgres` (without `su ... -c psql`) is a different thing: it tries to
start a second **server**, which PostgreSQL refuses to do as root.

### As a normal user, over TCP

```sh
psql -U fcatala- -d piscineds -h localhost -W
```

| Flag | Meaning |
|---|---|
| `-U name` | user (role) to connect as |
| `-d name` | database (can also be the first argument without a flag: `psql piscineds fcatala-`) |
| `-h host` | `localhost` = TCP connection; a path starting with `/` = socket folder; no `-h` = default socket |
| `-p port` | port, default `5432` (`-p` is **not** the password) |
| `-W` | always ask for the password |
| `-w` | never ask for the password (fails if none is available) |
| `-c "SQL"` | run one command and exit |
| `-f file.sql` | run the commands in a file and exit |
| `-t` / `-A` | print rows only (no header or footer) / no alignment. `-tAc` is handy in scripts |

Example: `psql -h localhost -U fcatala- -d piscineds -tAc "SELECT 1 + 1;"` prints just `2`.

**Socket or TCP?** Without `-h`, psql uses the local socket, governed by the `local`
lines of `pg_hba.conf`. With `-h localhost` it uses TCP, governed by the `host` lines.
Their authentication methods can differ (see section 4).

### Reading the prompt

| Prompt | Meaning |
|---|---|
| `piscineds=#` | ready, connected as a superuser |
| `piscineds=>` | ready, connected as a normal user |
| `piscineds-#` | statement not finished: missing `;` |
| `piscineds(#` | a parenthesis is still open |
| `piscineds'#` | a single-quoted string is still open |
| `piscineds"#` | a double-quoted name is still open |

### Meta-commands (start with `\`, no `;`)

| Command | What it does |
|---|---|
| `\q` | quit |
| `\l` | list databases |
| `\du` | list roles |
| `\dt` | list tables |
| `\d customers` | describe a table (columns, types, indexes) |
| `\x` | toggle vertical output (useful for wide rows) |
| `\echo 'text'` | print a message (useful in `.sql` files) |
| `\copy ...` | load a file from the client side (section 6) |

---

## 3. Roles and databases

### Create a user

```sql
CREATE USER "fcatala-" WITH ENCRYPTED PASSWORD 'mysecretpassword';
```

`CREATE USER` creates a role that can log in. Result: `CREATE ROLE`.
Passwords are always stored hashed (SCRAM-SHA-256 by default), so `ENCRYPTED` is accepted
but changes nothing.

To change the password later: `ALTER USER "fcatala-" PASSWORD 'newpassword';`

### Create a database owned by that user

```sql
CREATE DATABASE piscineds OWNER "fcatala-";
```

Result: `CREATE DATABASE`. The **owner** can create, change and drop everything inside
the database.

### OWNER versus GRANT

```sql
GRANT ALL PRIVILEGES ON DATABASE piscineds TO "fcatala-";
```

At database level, "all privileges" is only `CONNECT`, `CREATE` (schemas) and `TEMPORARY`.
It does **not** allow creating tables in the `public` schema, which since PostgreSQL 15
is no longer writable by everyone. A user with only this grant gets
`permission denied for schema public` on its first `CREATE TABLE`.
Making the user the **owner** avoids the problem.

### Quotes: names versus values

| Syntax | Used for | Case |
|---|---|---|
| `"fcatala-"` | identifiers (names of users, tables, columns) | kept exactly as written |
| `fcatala` (no quotes) | identifiers | folded to lowercase |
| `'mysecretpassword'` | string values | kept exactly as written |

A name containing a hyphen (`fcatala-`) **must** be double-quoted in SQL.
SQL keywords (`create`, `CREATE`) are case-insensitive.

---

## 4. Authentication

### pg_hba.conf

Each line says: for this connection type, database, user and address, use this method.

```
# TYPE  DATABASE  USER  ADDRESS        METHOD
local   all       all                  trust
host    all       all   127.0.0.1/32   scram-sha-256
host    all       all   ::1/128        scram-sha-256
```

| Type / method | Meaning |
|---|---|
| `local` | connections through the Unix socket (psql without `-h`) |
| `host` | TCP connections (psql `-h localhost`, Python, DBeaver) |
| `trust` | no check at all: any password, or none, is accepted |
| `peer` | the operating-system user must have the same name as the database role |
| `scram-sha-256` | the password is checked |

With `trust` on the `host` lines, `psql -h localhost -w` (no password) still connects.
After changing them to `scram-sha-256` and reloading, it fails with
`fe_sendauth: no password supplied`. A wrong password fails with
`password authentication failed`.

### Which file is really in use?

```sql
SHOW hba_file;          -- e.g. /etc/postgresql/pg_hba.conf
SHOW config_file;
SHOW data_directory;
```

Editing a copy the server doesn't read has no effect, so check this first.
These settings are only visible to a superuser (`sudo su postgres -c psql`).

### Reload, then check what the server loaded

```sql
SELECT pg_reload_conf();                       -- t = reload signal accepted
SELECT type, database, user_name, address, auth_method
FROM pg_hba_file_rules;                        -- the rules as the server holds them
```

`pg_hba_file_rules` shows the rules the server is **using**, which can differ from the
file if the reload didn't happen or the wrong file was edited. Superuser only.

### Giving the password without typing it

| Method | How |
|---|---|
| `PGPASSWORD` variable | `PGPASSWORD=... psql -h localhost ...` (visible to other processes: for tests only) |
| `~/.pgpass` file | one line per connection: `host:port:database:user:password`, file mode `600` |
| libpq variables | `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`: read by psql and psycopg2 |

There's no `--password=` flag on purpose: a password on the command line would appear in
the process list and in the shell history.

This project stores the libpq variables in a `.env` file (git-ignored), created by
`make getpass` once the password has been checked against the database.

---

## 5. Data types

The types used in the project, and why:

| Type | Range / meaning | Used for |
|---|---|---|
| `TIMESTAMP` | date and time | `event_time` |
| `VARCHAR(n)` | text up to `n` characters | `event_type` (≤ 16 chars → `VARCHAR(32)`), `brand` |
| `INTEGER` | ±2,147,483,647 | `product_id` (max ≈ 5.9 M) |
| `BIGINT` | ±9.2 × 10¹⁸ | `user_id` (≈ 600 M and growing), `category_id` (19 digits) |
| `NUMERIC(p,s)` | exact decimal: `p` digits, `s` after the point | `price` → `NUMERIC(10,2)` |
| `UUID` | 128-bit identifier | `user_session` |

- Use `NUMERIC` (not `REAL`/`FLOAT`) for money: floating point can't store `2.62` exactly.
- Choose `INTEGER` versus `BIGINT` from the **real maximum** in the data, with headroom for
  values that keep growing.
- `NOT NULL` makes the database reject rows without a value. Only use it when the data
  never has gaps: one empty field makes a whole load fail.

---

## 6. Creating tables and loading CSV files

### Create and drop

```sql
DROP TABLE IF EXISTS data_2022_oct;
CREATE TABLE data_2022_oct (
    event_time    TIMESTAMP     NOT NULL,
    event_type    VARCHAR(32)   NOT NULL,
    product_id    INTEGER,
    price         NUMERIC(10,2),
    user_id       BIGINT,
    user_session  UUID
);
```

`DROP TABLE IF EXISTS` doesn't fail when the table is missing (it prints a notice).
Dropping before creating makes a script safe to run again.

### COPY: bulk loading

```sql
COPY data_2022_oct (event_time, event_type, product_id, price, user_id, user_session)
FROM STDIN WITH (FORMAT csv, HEADER true, NULL '');
```

| Option | Meaning |
|---|---|
| `FORMAT csv` | parse as CSV (handles quotes and commas inside values) |
| `HEADER true` | skip the first line |
| `NULL ''` | an empty field becomes `NULL` (otherwise an empty number or UUID fails) |

`COPY` loads millions of rows in one operation, far faster than one `INSERT` per row.
Result: `COPY 4102283` (the number of rows loaded).

### Who reads the file?

| Command | File read by | Needs |
|---|---|---|
| `COPY t FROM '/path/file.csv'` | the **server** (user `postgres`) | the server must be able to read the path |
| `\copy t FROM 'file.csv' ...` (psql) | the **client** (you) | only your own permissions |
| `COPY t FROM STDIN` + psycopg2 `copy_expert` | the **client** (your script) | only your own permissions |

Files in your home folder are usually unreadable for the `postgres` user, so use the
client-side forms.

```sh
psql -h localhost -U fcatala- -d piscineds \
     -c "\copy data_2022_oct FROM 'data_2022_oct.csv' WITH (FORMAT csv, HEADER true, NULL '')"
```

---

## 7. Inspecting data and the catalog

### Counting

```sql
SELECT count(*)                      FROM items;   -- all rows
SELECT count(category_id)            FROM items;   -- rows where category_id IS NOT NULL
SELECT count(DISTINCT product_id)    FROM items;   -- distinct values
SELECT count(*) FILTER (WHERE category_id IS NULL) FROM items;  -- conditional count
```

`count(column)` ignores `NULL`s, so `count(*) - count(column)` is the number of missing
values. `FILTER (WHERE ...)` counts only the rows matching a condition, and several
filtered counts can be computed in one scan.

### Is a table there?

```sql
SELECT to_regclass('public.customers');   -- table name if it exists, NULL otherwise
```

### Listing tables and columns

```sql
-- tables whose name matches a regular expression (~ = regex match)
SELECT tablename FROM pg_tables
WHERE schemaname = 'public' AND tablename ~ '^data_20[0-9]{2}_[a-z]{3}$'
ORDER BY tablename;

-- columns of a table, in order
SELECT column_name FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'customers'
ORDER BY ordinal_position;

-- columns with their full SQL type
SELECT attname, format_type(atttypid, atttypmod)
FROM pg_attribute
WHERE attrelid = 'public.items'::regclass AND attnum > 0 AND NOT attisdropped
ORDER BY attnum;
```

The last query returns types such as `bigint` or `character varying(64)`, ready to reuse
in an `ALTER TABLE ... ADD COLUMN`. Reading names and types from the catalog lets a
script adapt to the schema instead of hardcoding it.

### Size on disk

```sql
SELECT pg_size_pretty(pg_total_relation_size('customers'));   -- e.g. 1758 MB
```

---

## 8. Building tables from other tables

### Copy a structure

```sql
CREATE TABLE customers (LIKE data_2022_oct INCLUDING ALL);   -- keeps NOT NULL, defaults, indexes
CREATE TABLE copy_of AS SELECT * FROM data_2022_oct;         -- copies data, loses constraints
```

Both copy the column types. Only `LIKE ... INCLUDING ALL` keeps the constraints:
on the project's tables it keeps the 2 `NOT NULL` columns, `CREATE TABLE AS` keeps none.

### Append rows

```sql
INSERT INTO customers SELECT * FROM data_2022_oct;
```

Result: `INSERT 0 4102283` (the second number is the rows inserted).
Repeating it for every monthly table works like `UNION ALL`: every row is kept,
duplicates included. Plain `UNION` would remove identical rows, and is much slower on
large tables.

### Change a table

```sql
ALTER TABLE customers ADD COLUMN brand VARCHAR(64);
ALTER TABLE customers DROP COLUMN IF EXISTS brand;
ALTER TABLE customers_new RENAME TO customers;
```

### Temporary tables, indexes and statistics

```sql
CREATE TEMP TABLE items_by_product AS SELECT ... ;      -- dropped at the end of the session
CREATE UNIQUE INDEX ON items_by_product (product_id);   -- fails if product_id has duplicates
ANALYZE customers;                                      -- refresh the planner's statistics
```

A unique index works as a guarantee as well as a speed-up: if the data broke the
assumption "one row per product", the index creation would fail.

### The rebuild-and-swap pattern

Instead of `DELETE` or `UPDATE` on millions of rows:

```sql
BEGIN;
CREATE TABLE customers_new (LIKE customers INCLUDING ALL);
INSERT INTO customers_new SELECT ... FROM customers ...;   -- the rows you want
DROP TABLE customers;
ALTER TABLE customers_new RENAME TO customers;
ANALYZE customers;
COMMIT;
```

It's faster, leaves no dead rows to vacuum, and inside a transaction it's all-or-nothing:
on error, the original table is untouched. Exercises 02 and 03 use it.

---

## 9. Window functions

A window function computes a value for each row from a set of related rows, without
merging them as `GROUP BY` would.

```sql
SELECT event_time,
       LAG(event_time) OVER (PARTITION BY event_type, product_id, price, user_id, user_session
                             ORDER BY event_time) AS prev_time
FROM customers;
```

| Part | Meaning |
|---|---|
| `LAG(col)` | value of `col` in the **previous** row of the window (`NULL` for the first) |
| `PARTITION BY ...` | rows are grouped by these columns (`NULL`s count as equal here) |
| `ORDER BY ...` | order of the rows inside each group |

Subtracting two timestamps gives an **interval**:

```
 event_time          | prev_time           | event_time - prev_time
 2022-10-01 00:00:32 |                     |
 2022-10-01 00:00:33 | 2022-10-01 00:00:32 | 00:00:01   <- re-sent 1 s later
 2022-10-01 00:00:40 | 2022-10-01 00:00:33 | 00:00:07   <- a new action
```

The condition `event_time - prev_time <= interval '1 second'` is exercise 02's duplicate
rule.

---

## 10. Aggregate functions

```sql
SELECT product_id,
       mode() WITHIN GROUP (ORDER BY brand)                     AS most_frequent,
       array_agg(DISTINCT brand) FILTER (WHERE brand IS NOT NULL) AS all_brands,
       count(DISTINCT brand)                                     AS n_brands
FROM items
GROUP BY product_id
HAVING count(*) > 1;
```

| Function | Result |
|---|---|
| `mode() WITHIN GROUP (ORDER BY x)` | most frequent value; ignores `NULL`s; on a tie, the first in `ORDER BY` order |
| `array_agg(DISTINCT x)` | all distinct values as an array, e.g. `{10,20}` |
| `... FILTER (WHERE ...)` | only the rows matching the condition are aggregated |
| `cardinality(array)` | number of elements in an array |
| `string_agg(x, ', ')` | values joined into one text, e.g. `10, 20` |
| `HAVING ...` | condition on the groups (after `GROUP BY`), where `WHERE` filters rows |

Example: for the values `20, 10, 10, NULL`, `mode()` gives `10` and `array_agg(DISTINCT ...)
FILTER (WHERE ... IS NOT NULL)` gives `{10,20}`.

`string_agg` is PostgreSQL's equivalent of MySQL's `GROUP_CONCAT` (see section 16).

---

## 11. Common table expressions (WITH)

### A named subquery

```sql
WITH s AS MATERIALIZED (
    SELECT *, LAG(event_time) OVER (PARTITION BY product_id ORDER BY event_time) AS prev
    FROM customers
)
SELECT count(*) FROM s WHERE prev IS NULL;
```

`WITH name AS (...)` names a subquery for the query that follows. `MATERIALIZED` asks
PostgreSQL to compute it **once** and reuse the result, which matters when it's
expensive and read twice.

### Writing to two tables in one statement

```sql
WITH s AS MATERIALIZED (
    SELECT *, LAG(event_time) OVER (PARTITION BY product_id ORDER BY event_time) AS prev
    FROM customers
),
d AS (
    INSERT INTO duplicates
    SELECT event_time, event_type, product_id, price, user_id, user_session
    FROM s WHERE prev IS NOT NULL AND event_time - prev <= interval '1 second'
)
INSERT INTO kept
SELECT event_time, event_type, product_id, price, user_id, user_session
FROM s WHERE prev IS NULL OR event_time - prev > interval '1 second';
```

An `INSERT` inside `WITH` is a **data-modifying CTE**. Here one pass over `customers`
sends each row to one of two tables. Exercise 02 uses this to remove duplicates and keep
them in `dups_customer` without reading the data twice.
(For readability this example groups by `product_id` only; the project groups by every
column except `event_time`.)

---

## 12. Joins

### LEFT JOIN keeps unmatched rows

```sql
SELECT c.*, i.category_id, i.brand
FROM customers c
LEFT JOIN items_by_product i ON c.product_id = i.product_id;
```

Every customer row is kept. When no item matches, the item columns are `NULL`.
An `INNER JOIN` (plain `JOIN`) would silently drop those rows.

### A join can multiply rows

If `items` has two rows for a product, every event of that product appears **twice**
after the join. Join only on a key that is unique on that side: collapse the table to one
row per key first, or join on `(SELECT DISTINCT product_id FROM items)`.

### Rows with no match (anti-join)

```sql
SELECT count(*) FROM customers c
WHERE NOT EXISTS (SELECT 1 FROM items i WHERE i.product_id = c.product_id);
```

Counts the events whose product is not in `items`.

### Check how a query will run

```sql
EXPLAIN SELECT count(*) FROM customers c
LEFT JOIN (SELECT DISTINCT product_id FROM items) p ON p.product_id = c.product_id;
```

`EXPLAIN` shows the plan without running the query. Look for one `Seq Scan on customers`
and a `Hash Join`: the big table is read once.

---

## 13. Transactions and session settings

### Transactions

```sql
BEGIN;
-- several statements
COMMIT;      -- make all the changes permanent
-- or
ROLLBACK;    -- undo all the changes since BEGIN
```

Inside a transaction, the changes are **all or nothing**. This is what lets the scripts
check their results (row counts, conservation) and undo everything if a check fails.

### SET versus SET LOCAL

```sql
SET work_mem = '256MB';         -- for the rest of the session
BEGIN;
SET LOCAL work_mem = '256MB';   -- only until COMMIT / ROLLBACK
COMMIT;
SHOW work_mem;                  -- back to the default (e.g. 4MB)
```

`work_mem` is the memory each sort or hash may use before spilling to disk. More memory
speeds up big sorts, such as the window function in exercise 02. `SET LOCAL` limits the
change to one transaction.

---

## 14. Cleaning up

### Drop known tables

```sql
DROP TABLE IF EXISTS data_2022_oct, data_2022_nov, data_2022_dec, data_2023_jan;
```

### Drop every table in a schema

```sql
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
        EXECUTE 'DROP TABLE IF EXISTS ' || quote_ident(r.tablename) || ' CASCADE';
    END LOOP;
END $$;
```

`DO $$ ... $$` runs an anonymous PL/pgSQL block. The loop drops each table found in the
catalog. `quote_ident` quotes names safely, and `CASCADE` also drops the objects that
depend on each table. The database, the users and the configuration are not touched.
This is what `utils/reset_db.py` (`make clean`) runs.

A harsher alternative removes **everything** in the schema (tables, views, functions):

```sql
DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
```

### Reclaim space

```sql
VACUUM FULL customers;   -- rewrites the table without the space left by deleted rows
```

Needed after large `DELETE`s, because deleted rows still occupy space. The
rebuild-and-swap pattern (section 8) avoids the problem.

---

## 15. Using PostgreSQL from Python (psycopg2)

```python
import psycopg2
from psycopg2 import sql

conn = psycopg2.connect(dbname="piscineds", user="fcatala-",
                        host="localhost", port=5432)   # no password: libpq reads
                                                       # PGPASSWORD or ~/.pgpass
try:
    with conn:                         # transaction: commit on success, rollback on error
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM items WHERE brand = %s;", ("runail",))
            (n,) = cur.fetchone()      # one row, as a tuple

            cur.execute(sql.SQL("DROP TABLE IF EXISTS {};")
                        .format(sql.Identifier("data_2022_oct")))

            with open("data_2022_oct.csv") as f:
                cur.copy_expert("COPY data_2022_oct FROM STDIN "
                                "WITH (FORMAT csv, HEADER true, NULL '')", f)
            print(cur.rowcount)        # rows affected by the last statement
finally:
    conn.close()                       # always release the connection
```

| Element | Use |
|---|---|
| `%s` + a tuple of values | pass **values** safely (never build SQL with f-strings and user data) |
| `sql.Identifier(name)` | pass a **table or column name** safely (quoted when needed) |
| `sql.Literal(value)` | insert a constant into a composed query |
| `copy_expert(sql, file)` | client-side `COPY ... FROM STDIN`: the script streams the file |
| `cur.rowcount` | rows inserted / updated / deleted by the last statement |
| `with conn:` | one transaction; note it does **not** close the connection |
| `getpass.getpass()` | read a password from the terminal without showing it |

---

## 16. Common pitfalls

| Symptom | Cause | Fix |
|---|---|---|
| syntax error near `fcatala` | hyphen in an unquoted name | `"fcatala-"` |
| `permission denied for schema public` | user has database-level grants only | make the user the database **owner** |
| any password (or none) is accepted | `trust` in `pg_hba.conf` | `scram-sha-256` on the `host` lines, then reload |
| edits to `pg_hba.conf` have no effect | wrong file, or no reload | `SHOW hba_file;`, `SELECT pg_reload_conf();`, check `pg_hba_file_rules` |
| `GROUP_CONCAT` does not exist | MySQL syntax | `string_agg(col, ', ')` |
| filter by database name in `information_schema` returns nothing | in PostgreSQL a database contains schemas | filter by `table_schema = 'public'` |
| `COPY ... FROM '/home/...'` fails with permission denied | the server reads the file | `\copy` or `COPY ... FROM STDIN` |
| a numeric column fails to load on empty fields | empty string is not a number | `NULL ''` in the `COPY` options |
| row count grows after a join | duplicate keys on the joined side | collapse to one row per key first |
| rows disappear after a join | `INNER JOIN` drops unmatched rows | `LEFT JOIN` |
| `CREATE TABLE AS` lost the `NOT NULL`s | it copies data and types only | `CREATE TABLE ... (LIKE ... INCLUDING ALL)` |
| `~/data` path not found from Python | `~` is a shell feature | `os.path.expanduser("~/data")` |
| `sudo postgres` refuses to run as root | that starts a server, not a client | `sudo su postgres -c psql` |
