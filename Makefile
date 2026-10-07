# Set the default goal so running `make` with no arguments prints the help menu
.DEFAULT_GOAL := database

.PHONY: help
help: ## Show this help menu
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z0-9_-]+:.*?## / {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.PHONY: ex00
ex00: database ## Regenerates the project from scratch ready to inspect
	@echo "Please open DBeaver to inspect the database for this exercise."

.PHONY: ex01
ex01: ## Join all the data_202*_*** tables together in a table called "customers"
	@./ex01/customers_table.py

.PHONY: ex02
ex02: ## Executes ex02 only with the directory customer
	@./utils/decompress_customer.sh
	@./ex02/table.py

.PHONY: ex03
ex03: ## Executes ex03 only with the directory customer
	@./utils/decompress_customer.sh
	@./ex03/automatic_table.py

.PHONY: ex04
ex04: ## Executes ex04 only with the directory items
	@./utils/decompress_items.sh
	@./ex04/items_table.py

.PHONY: fclean
fclean: ## Cleans up database as its previous state before the first run
	@./utils/reset_db.py

.PHONY: re
re: fclean database ## Cleans up database and recreates it

.PHONY: database
database: ## Charges the full database (default target)
	@./utils/automatic_table.py
	@./utils/items_table.py
