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
