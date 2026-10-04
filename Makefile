# Set the default goal so running `make` with no arguments prints the help menu
.DEFAULT_GOAL := database

.PHONY: help
help: ## Show this help menu
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z0-9_-]+:.*?## / {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.PHONY: ex00
ex00: ## Show the instructions for the project
	@count=0; \
	while IFS= read -r line || [ -n "$$line" ]; do \
		printf '%s\n' "$$line" | cat -e; \
		count=$$((count + 1)); \
		if [ $$((count % 30)) -eq 0 ]; then \
			printf '\033[32mPress Enter for the next 30 lines...\033[0m\n'; \
			read -r _ < /dev/tty; \
		fi; \
	done < ex00/VM-instructions.txt

.PHONY: ex01
ex01: ## Requires the evaluator to open DBeaver to inspect the database.
	echo "Please open DBeaver to inspect the database for this exercise."


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

.PHONY: database
database: ## Charges the full database
	@./utils/automatic_table.py
	@./utils/items_table.py
