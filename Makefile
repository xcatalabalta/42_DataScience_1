# Set the default goal so running `make` with no arguments charges the full database
.DEFAULT_GOAL := database

# Targets run strictly one after another, even if someone calls `make -j`
.NOTPARALLEL:

# --- Settings ----------------------------------------------------------------
# env_sample.txt (committed) holds every setting except the password.
# .env (NOT committed, see .gitignore) is created by `make getpass` from
# env_sample.txt, adding the password once it has been checked against the
# database. .env is read after env_sample.txt, so its values win.
ENV_SAMPLE := env_sample.txt
ENV_FILE   := .env

ifeq ($(wildcard $(ENV_SAMPLE)),)
$(error $(ENV_SAMPLE) not found: it holds the project settings)
endif
include $(ENV_SAMPLE)
-include $(ENV_FILE)

# libpq names: psql and psycopg2 use them directly in every recipe
export PGHOST PGPORT PGDATABASE PGUSER PGPASSWORD DATA_CUSTOMER DATA_ITEMS

# --- Unattended mode ---------------------------------------------------------
# `make redo` calls the targets below with AUTO=1, which adds the flags that
# skip the confirmations. Run by hand, every target still asks.
AUTO ?=
YES  := $(if $(AUTO),--yes)
KEEP := $(if $(AUTO),--keep)

.PHONY: ex00
ex00: database ## Regenerates the project from scratch ready to inspect
	@echo "Please open DBeaver to inspect the database for this exercise."

.PHONY: ex01
ex01: ## Join all the data_202*_*** tables together in a table called "customers"
	@chmod +x ex01/customers_table.py
	@./ex01/customers_table.py $(KEEP)

.PHONY: ex02
ex02: ## Removes duplicates
	@chmod +x ex02/remove_duplicates.py
	@./ex02/remove_duplicates.py --check $(YES)

.PHONY: ex03
ex03: ## Combine the customers tables with items in the customers table
	@chmod +x ex03/fusion.py
	@./ex03/fusion.py $(YES)

.PHONY: database
database: getpass ## Charges the full database (default target)
	@./utils/automatic_table.py
	@./utils/items_table.py

.PHONY: getpass
getpass: ## Creates .env from env_sample.txt once the database password is checked
	@if [ -f $(ENV_FILE) ] \
		&& PGPASSWORD="$$(sed -n 's/^PGPASSWORD=//p' $(ENV_FILE))" \
			psql -w -tAc 'SELECT 1' >/dev/null 2>&1; then \
		echo "$(ENV_FILE) present and its password is valid."; \
	else \
		trap 'stty echo 2>/dev/null' EXIT INT TERM; \
		printf "Password for PostgreSQL user '$(PGUSER)': "; \
		stty -echo 2>/dev/null; read -r pw; stty echo 2>/dev/null; echo; \
		if err=$$(PGPASSWORD="$$pw" psql -w -tAc 'SELECT 1' 2>&1 >/dev/null); then \
			umask 077; \
			grep -v '^PGPASSWORD=' $(ENV_SAMPLE) > $(ENV_FILE); \
			printf 'PGPASSWORD=%s\n' "$$pw" >> $(ENV_FILE); \
			echo "Password verified: $(ENV_FILE) created from $(ENV_SAMPLE)."; \
		else \
			echo "ERROR: $(ENV_FILE) not created. PostgreSQL said:" >&2; \
			echo "$$err" >&2; exit 1; \
		fi; \
	fi

.PHONY: delpass
delpass: ## Deletes .env (the password); every script will ask for it again
	@rm -f $(ENV_FILE)
	@echo "$(ENV_FILE) removed. Every script will ask for the password again."

.PHONY: clean
clean: ## Drops all the tables (database, user and .env are kept)
	@chmod +x utils/reset_db.py
	@./utils/reset_db.py $(YES)

.PHONY: fclean
fclean: clean delpass ## Drops all the tables and deletes .env (before an evaluation)

.PHONY: help
help: ## Show this help menu
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z0-9_-]+:.*?## / {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.PHONY: inspect
inspect: ## Runs inspect SQL instructions for the items table
	psql -h /run/postgresql -U $(PGUSER) -d $(PGDATABASE) -f utils/inspect_items.sql

.PHONY: re
re: clean database ## Drops all the tables and recreates the database

.PHONY: redo
redo: getpass ## Redoes the full module without confirmations (monthly tables kept)
	@$(MAKE) --no-print-directory AUTO=1 re ex01 ex02 ex03
