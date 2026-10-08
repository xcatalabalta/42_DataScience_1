# Piscine DataScience — Module 1: Data Warehouse

This project is the **data warehouse** module of 42's Outer Core *Piscine Data Science*.
It introduces **ETL** (*Extract, Transform, Load*): a data integration process that
combines data from several sources into a single, consistent data store, loaded into a
data warehouse.

In this project the three stages are:

| Stage | What it means here |
|---|---|
| **Extract** | Read the raw CSV files: one file of customer events per month, plus an item catalogue. |
| **Transform** | Join the monthly tables, remove duplicate events, and merge the item data into each event. |
| **Load** | Store the result in one PostgreSQL table, `customers`, ready to be analysed in the next modules. |

---

## Project purpose

The repository is designed to:

- **Load the raw data** into PostgreSQL with typed tables (one table per monthly CSV, plus
  `items`), checking that every CSV row reaches the database.
- **Build the `customers` table** by joining all the `data_202*_***` tables, comparing
  each table's rows with the rows appended.
- **Remove duplicates**: exact repeats, and the events the server re-sends within 1 second.
  The removed rows are kept in `dups_customer`, so no deletion goes untraced.
- **Merge the item data** into `customers` without losing or multiplying any row. When the
  catalogue gives a product conflicting values, the alternatives are kept in
  `items_conflicts`.
- **Run step by step or in one go**: each exercise has its own `make` target, and
  `make redo` rebuilds the whole module unattended, with reproducible results.
- **Keep the password out of git**: settings live in `env_sample.txt`; the password lives
  only in a local `.env` that git ignores.
- **Reset between evaluations**: `make fclean` returns the database and the project to
  their initial state.

---

## Repository structure

```
.
├── Makefile                 # entry point: every step is a make target
├── env_sample.txt           # project settings (no password), committed
├── ex00/                    # exercise 00: no file to turn in (database viewer)
├── ex01
│   └── customers_table.py   # exercise 01: join the monthly tables into "customers"
├── ex02
│   └── remove_duplicates.py # exercise 02: remove duplicate rows from "customers"
├── ex03
│   └── fusion.py            # exercise 03: combine "customers" with "items"
├── img
│   └── DBeaver_ex00.png     # images related to the outputs of the project
└── utils
    ├── automatic_table.py   # load one table per monthly CSV
    ├── inspect_items.sql    # checks on "items" before the fusion
    ├── items_table.py       # load the "items" table
    └── reset_db.py          # drop all the tables (keeps database, user, password)
```

Created locally, not committed:

```
.env                         # env_sample.txt + the database password (git-ignored)
```

The data stays outside the repository (its location is set in `env_sample.txt`):

```
~/data_piscineds/data/
├── customer/                # data_2022_oct.csv ... data_2023_feb.csv
└── items/                   # item.csv
```

### Key folders

| Folder | Content |
|---|---|
| `ex00/` | *Show me your DB*. Nothing to turn in: the database is browsed with DBeaver. |
| `ex01/` | *customers table*. Joins every `data_202*_***` table into `customers`. |
| `ex02/` | *remove duplicates*. Removes exact duplicates and 1-second re-sends from `customers`. |
| `ex03/` | *fusion*. Adds the item columns to `customers` without losing information. |
| `img/` | Images related to the execution of the product (used as documentation). |
| `utils/` | Support scripts that are not exercises: loading the raw data, inspecting it, and resetting the database. |

---

## Workflow

### Prerequisites

- PostgreSQL running, with the `piscineds` database owned by your user (module 0).
- Python 3 with `psycopg2`.
- The CSV files in the folders set in `env_sample.txt`.

### First run: the password

```sh
make getpass
```

This asks for the database password, checks it by connecting, and only then creates `.env`
from `env_sample.txt`. Every script reads `.env`, so the password is not asked again.

If `.env` is missing or its password no longer works, any target that needs the database
asks for it again.

### Step by step

Run the targets in this order to follow the exercises one at a time:

| Command | What it does |
|---|---|
| `make database` | Loads the monthly tables and `items` (this is also the default: plain `make`). |
| `make ex00` | Same as `make database`, then a reminder to open DBeaver. |
| `make ex01` | Builds `customers`, then asks whether to drop the monthly tables. |
| `make ex02` | Counts the duplicates, asks for confirmation, removes them and verifies none remain. |
| `make inspect` | Shows the checks on `items` that the fusion depends on. |
| `make ex03` | Asks for confirmation, then merges `items` into `customers`. |

Every step that changes or deletes data asks for confirmation first.

### In one run

```sh
make redo
```

This rebuilds the whole module from scratch with no confirmation prompts:
drop all tables → load the data → ex01 → ex02 → ex03.
The monthly tables are kept, so they can still be inspected afterwards.

### Reset

| Command | What it does |
|---|---|
| `make clean` | Drops all the tables. The database, the user and `.env` are kept. |
| `make re` | `clean`, then `database`. |
| `make fclean` | `clean`, then deletes `.env`. Run it before each evaluation. |

`make help` lists all the targets.

---

## Solutions by exercise

Every script follows the same conventions:

- It connects with the settings from `.env` / `env_sample.txt`, and asks for the password
  only if none is stored.
- It works inside a transaction: if anything fails, the database is left as it was.
- It quotes table and column names safely (`psycopg2.sql.Identifier`).
- It asks before changing or deleting data. `--yes` skips the question; `make redo` passes it.

### Data loading (`utils/`): prerequisite of every exercise

**Main strategy**
- `automatic_table.py` creates one table per CSV in the customer folder, named after the
  file (`data_2022_oct.csv` → `data_2022_oct`), all with the same schema:
  `TIMESTAMP`, `VARCHAR`, `INTEGER`, `NUMERIC`, `BIGINT`, `UUID`.
- `items_table.py` creates `items` from `item.csv` (`INTEGER`, `BIGINT`, `VARCHAR`).
  All its columns accept NULL, because many values are missing in the catalogue.
- Both load with `COPY ... FROM STDIN`. The script streams the file itself, so the
  PostgreSQL server doesn't need read access to your home folder.
- Each table is dropped and recreated, so the load can be rerun safely.

**Guards**
- Each CSV is loaded in its own transaction: a failing file is reported, the others are
  still loaded, and the script ends with a non-zero exit code.
- Empty CSV fields are loaded as `NULL` (`NULL ''`), so a missing value can't abort the
  load of millions of rows.
- For each table, the rows loaded are compared with the data lines of its CSV, and any
  difference is reported as "row(s) skipped".
- A missing data folder or CSV file stops the script with a clear message.

**Expected output** (`make database`)

```
Password for PostgreSQL user 'fcatala-':           <- only if .env does not exist yet
Password verified: .env created from env_sample.txt.
Found 5 CSV file(s) in /home/fcatala-/data_piscineds/data/customer:
...
Processing data_2022_dec.csv ...
  OK: table data_2022_dec loaded with 3533286 rows.
  Original file contains 3533286 rows of data.
  0 row(s) skipped.
...
Done. 5 table(s) loaded, 0 failed.
...
Done. items now contains 109579 rows.
Original CSV file contains 109579 rows of data.
0 row(s) skipped.
```

| Table | Rows loaded | Rows in CSV |
|---|---:|---:|
| `data_2022_oct` | 4,102,283 | 4,102,283 |
| `data_2022_nov` | 4,635,837 | 4,635,837 |
| `data_2022_dec` | 3,533,286 | 3,533,286 |
| `data_2023_jan` | 4,264,752 | 4,264,752 |
| `data_2023_feb` | 4,156,682 | 4,156,682 |
| **total** | **20,692,840** | |
| `items` | 109,579 | 109,579 |

### Exercise 00: Show me your DB

**Main strategy**
- DBeaver Community (Flatpak) connects to `piscineds` on `localhost:5432` with the project
  user and password. That's the same TCP and password route the scripts use.
- Records can be found by ID with the filter bar of a table's *Data* tab
  (e.g. `product_id = 5779403`), or by right-clicking a cell → *Filter*.

**Guards**
- None: there is no file to turn in. `make ex00` only rebuilds the data and reminds you to
  open DBeaver.

**Expected output**

<img width="931" height="417" alt="image" src="https://github.com/user-attachments/assets/4efc7f54-de05-4d95-a4fb-1ac3aafbafa7" />

### Exercise 01: customers table

**Main strategy**
- The monthly tables are found in the catalog (`pg_tables`, name matching
  `^data_20[0-9]{2}_[a-z]{3}$`), not hardcoded. A new month is picked up automatically.
- `customers` is created with `CREATE TABLE ... (LIKE <first monthly table> INCLUDING ALL)`.
  This keeps the exact column types and `NOT NULL` constraints, which
  `CREATE TABLE ... AS SELECT` would lose.
- Each monthly table is appended with `INSERT ... SELECT`, which keeps every row like
  `UNION ALL`. Duplicates are kept on purpose: removing them is exercise 02.
- After the build is saved, the script offers to drop the monthly tables to free disk
  space (`--keep` keeps them, `--yes` drops them without asking).

**Guards**
- Every CSV in the data folder must have a loaded table, otherwise the script stops before
  building an incomplete `customers`. Tables without a CSV are reported as a warning.
- For each table, the rows appended must equal the rows in the original table.
- The final row count of `customers` must equal the sum of the monthly tables.
- Any failed check undoes the whole build.
- The monthly tables can only be dropped after `customers` has been saved and checked.

**Expected output** (`make ex01`)
```
Joining 5 table(s) into customers: data_2022_dec, data_2022_nov, data_2022_oct, data_2023_feb, data_2023_jan
Dropping table customers if it exists ...
Creating table customers (same structure as data_2022_dec) ...
Appending tables (original rows vs appended rows):
  data_2022_dec   original    3533286  appended    3533286  OK
  data_2022_nov   original    4635837  appended    4635837  OK
  data_2022_oct   original    4102283  appended    4102283  OK
  data_2023_feb   original    4156682  appended    4156682  OK
  data_2023_jan   original    4264752  appended    4264752  OK
Done. customers now contains 20692840 rows (sum of the monthly tables).

The following table(s) are now contained in customers:
  - data_2022_dec
  ...
Drop them from the database to free space? [y/N] n
Cleanse skipped. Monthly tables kept.
```

What proves the result:
- every monthly table is appended in full (`original` = `appended`, `OK` on each line);
- `customers` has **20,692,840** rows, the sum of the five monthly tables;
- duplicates are still there on purpose: they are removed in exercise 02.

With `make redo` the cleanse question is skipped (`--keep`):
`Cleanse skipped (--keep). Monthly tables kept.`

### Exercise 02: remove duplicates

**Main strategy**
- **Definition of a duplicate:** a row identical to the previous one in **every column
  except `event_time`**, arriving **at most 1 second later**. `LAG(event_time)` gives the
  previous time, over the rows grouped by all the other columns and ordered by time.
  This catches both cases in one rule:
  - exact duplicate: gap of 0 s → removed (`exact`)
  - server re-send: gap of 1 s → removed (`resend`)
  - the same action later: gap > 1 s → kept, it is a genuine new event
- A chain of re-sends (0 s, 1 s, 2 s, ...) collapses to its first row, because each row is
  compared with the row just before it.
- The columns are read from the catalog, so the rule still applies if the schema changes.
- **Rebuild and swap in one pass:** the gaps are computed once, and a single statement
  sends the kept rows to a new table and the duplicates to `dups_customer`. The new table
  then replaces `customers`. This is much faster than `DELETE` on ~20 M rows, and leaves no
  dead rows to clean up.
- `dups_customer` keeps every removed row, plus the time of the row it repeats
  (`prev_event_time`) and its kind (`dup_kind`: `exact` or `resend`).

**Guards**
- **Conservation check:** rows kept + duplicates must equal the original count, so no row
  can be lost or invented.
- With `--check`: the duplicates are counted before asking for confirmation, the number
  removed must equal that count, and a final pass verifies that no duplicate remains.
- A rerun on already-clean data finds 0 duplicates and keeps the previous `dups_customer`
  instead of replacing it with an empty table.
- The script stops if `customers` or its `event_time` column is missing.
- Everything runs in one transaction: on any error, `customers` is untouched.

**Expected output** (`make ex02`)

```
customers: 20692840 rows
Duplicate = same event_type, product_id, price, user_id, user_session and event_time <= 1 second after the previous one
Counting duplicates (--check) ...
  exact duplicates (0 s)       : 1109098
  re-sends (<= 1 second)       : 407843
Remove duplicates from customers (20692840 rows) and store them in dups_customer? [y/N] y
Splitting rows into kept rows and duplicates ...
Done in 210 s.
  customers     : 20692840 -> 19175899 rows
  dups_customer : 1516941 rows (7.33%) exact=1109098 resend=407843
  conservation  : 19175899 + 1516941 = 20692840 OK
Verifying (--check) ...
  OK: no duplicate remains.
```

| | Rows |
|---|---:|
| `customers` before | 20,692,840 |
| exact duplicates (0 s) | 1,109,098 |
| re-sends (≤ 1 s) | 407,843 |
| **removed**, stored in `dups_customer` | **1,516,941** (7.33%) |
| **`customers` after** | **19,175,899** |

What proves the result:
- the duplicates removed are exactly the ones counted before asking (1,109,098 + 407,843);
- **conservation:** rows kept + rows removed = original rows, so no row was lost;
- the final pass finds no duplicate left in `customers`.

To see the subject's example (a 1-second re-send) in the removed rows:

```sql
SELECT event_time, prev_event_time, dup_kind, event_type, product_id
FROM dups_customer
WHERE product_id = 5779403
ORDER BY event_time
LIMIT 5;
```

### Exercise 03: fusion

**Main strategy**

`utils/inspect_items.sql` (`make inspect`) showed three facts that shape the fusion:

1. `items` is **not unique per product** (109,579 rows for 54,043 products). A plain join
   would multiply customer rows, so `items` is first collapsed to **one row per product**.
2. Some repeated rows hold **conflicting values**. Rule, per product and per column: keep
   the **most frequent non-NULL value**, and the smallest one on a tie
   (`mode() WITHIN GROUP`). NULLs are ignored, so rows that only complete each other
   merge cleanly. Every product with a conflict is stored in `items_conflicts`, with all
   its alternative values and the one chosen, so no information is lost.
3. Some customer events have **no product in `items`**. A `LEFT JOIN` keeps them, with
   empty item columns.

The script then:
- adds the item columns, with their types read from the `items` table;
- rebuilds `customers` as `customers LEFT JOIN items` and swaps it in (same pattern as
  exercise 02);
- reads `customers` only twice: once to count the rows and the events without an item,
  once to build the new table;
- prints the time taken by each step.

**Guards**
- The collapsed `items` has a unique index on `product_id`, so the join can't multiply rows.
- The row count of `customers` must be the same before and after, otherwise everything is
  undone.
- A rerun is safe: item columns from a previous run are dropped and recomputed, never
  duplicated, and the result is identical.
- The script stops if `customers`, `items` or the `product_id` column is missing.

**Expected output** (`make ex03`)

```
customers: 19175899 rows (1340 with a product not in items) | items: 109579 rows
Item columns to add: category_id, category_code, brand
Fuse items into customers (19175899 rows)? [y/N] y
items collapsed to 54043 products (one row per product)
items_conflicts: 1637 products with conflicting values (most frequent kept):
  category_id   : 1617
  category_code : 0
  brand         : 24
Done. customers: 19175899 -> 19175899 rows (no row lost, none multiplied)
  events matched in items  : 19174559
  events not in items      : 1340 (kept, item columns NULL)

Timing:
  connect                           :      0.02 s
  profile customers (1 pass)        :      6.27 s
  collapse items                    :      0.48 s
  save conflicts                    :      0.56 s
  build fused table (1 pass)        :     51.64 s
  swap + analyze                    :      1.90 s
  commit                            :      0.12 s
  processing (database work)        :     60.99 s
  total (incl. user input)          :       ...
```

| | Count |
|---|---:|
| `items` rows → products (one row each) | 109,579 → 54,043 |
| products with conflicting values (`items_conflicts`) | 1,637 (≈3%) |
| └ conflicts in `category_id` / `category_code` / `brand` | 1,617 / 0 / 24 |
| `customers` before → after | 19,175,899 → 19,175,899 |
| events matched in `items` | 19,174,559 |
| events not in `items` (kept, item columns empty) | 1,340 |

What proves the result:
- **same row count before and after**: no event lost, none duplicated by the join;
- matched + not matched = total (19,174,559 + 1,340 = 19,175,899);
- the 1,340 events without an item are the ones `make inspect` found before the fusion;
- every discarded alternative value is kept in `items_conflicts`, so no information is lost.

The timer pauses while waiting for the confirmation: *processing* is the database work;
*total* also includes the time spent answering the prompt.

To see the alternatives kept for a product with a brand conflict:

```sql
SELECT product_id, item_rows, brand_values, brand_chosen
FROM items_conflicts
WHERE cardinality(brand_values) > 1
LIMIT 10;
```
---

## Conclusion: results at a glance

The pipeline takes ~20.7 M raw events from five monthly CSV files to a single, clean,
enriched `customers` table. Every step is checked and every removed or discarded value
is kept:

| Step | Result in `customers` | Rows | Change |
|---|---|---:|---|
| Load (`make database`) | — (5 monthly tables + `items`) | 20,692,840 | every CSV row loaded, 0 skipped |
| ex01: customers table | all months joined | 20,692,840 | nothing lost: rows appended = rows loaded |
| ex02: remove duplicates | duplicates removed | 19,175,899 | −1,516,941 (7.33%), moved to `dups_customer` |
| ex03: fusion | item columns added | 19,175,899 | ±0 rows; 1,340 events without item kept |

Tables left in the database after `make redo`:

| Table | Rows | Role |
|---|---:|---|
| `customers` | 19,175,899 | the data warehouse table: deduplicated events + item data |
| `data_2022_oct` … `data_2023_feb` | 20,692,840 in total | raw monthly tables, kept for inspection |
| `items` | 109,579 | raw item catalogue |
| `dups_customer` | 1,516,941 | every duplicate removed, with the row it repeats and its kind |
| `items_conflicts` | 1,637 | products with conflicting item values: all alternatives + the one chosen |

In ETL terms:
- **Extract:** five monthly CSV files and one catalogue, loaded into typed tables with no
  row lost.
- **Transform:** months joined, duplicates removed by one explicit rule, item data merged
  with a documented conflict rule.
- **Load:** one consistent `customers` table, ready for the analysis modules.

The whole module can be rebuilt from scratch with one command (`make redo`) and gives the
same numbers on every run.
