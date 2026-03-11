#!/usr/bin/env bash
# Rebuild the DuckDB database from seed JSON files
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DB_PATH="$PROJECT_DIR/data/landscape.duckdb"

echo "Removing existing database..."
rm -f "$DB_PATH" "$DB_PATH.wal"

echo "Rebuilding from seed data..."
"$PROJECT_DIR/.venv/bin/landscape" import --seed

echo "Done."
"$PROJECT_DIR/.venv/bin/landscape" stats
