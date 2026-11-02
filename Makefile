# Loblaw Bio immune profiling: build, analyse, serve.
#
#   make setup      install dependencies into a local virtual environment (.venv)
#   make pipeline   build teiko.db from cell-count.csv and write Parts 2-4 to outputs/
#   make dashboard  start the dashboard on http://localhost:8000 (next free port if busy)
#   make test       run the test suite
#   make lint       check formatting and lint rules

VENV := .venv
# Use the virtual environment once `make setup` has created it, otherwise the system Python.
# Deferred (=) and checked with the shell, so `make setup pipeline` in one call works.
PYTHON = $(shell [ -x $(VENV)/bin/python ] && echo $(VENV)/bin/python || echo python3)

.PHONY: setup pipeline dashboard test lint clean

setup:
	python3 -m venv $(VENV)
	$(VENV)/bin/python -m pip install --upgrade pip --quiet
	$(VENV)/bin/python -m pip install -r requirements.txt --quiet
	@echo "Setup complete. Next: make pipeline"

pipeline:
	$(PYTHON) load_data.py
	$(PYTHON) run_analysis.py

dashboard:
	$(PYTHON) serve.py

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m ruff check .

clean:
	rm -rf teiko.db outputs .pytest_cache .ruff_cache
