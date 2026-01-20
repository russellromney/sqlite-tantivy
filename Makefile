.PHONY: build release test test-unit test-integration clean check lint fmt

# Build debug version
build:
	cargo build

# Build release version
release:
	cargo build --release

# Run all tests
test: test-unit test-integration

# Run unit tests
test-unit:
	cargo test --lib

# Run integration tests (requires built extension)
test-integration: build
	cargo test --test integration

# Clean build artifacts
clean:
	cargo clean

# Run clippy lints
lint:
	cargo clippy -- -D warnings

# Check formatting
fmt-check:
	cargo fmt -- --check

# Format code
fmt:
	cargo fmt

# Full check (format + lint + test)
check: fmt-check lint test

# Load extension in sqlite3 CLI for manual testing
repl: release
	sqlite3 :memory: ".load ./target/release/libsqlite_tantivy"

# Show extension file location
show-lib:
	@echo "Debug:   ./target/debug/libsqlite_tantivy.dylib"
	@echo "Release: ./target/release/libsqlite_tantivy.dylib"

# ========================================
# Corpus Management
# ========================================

# List available corpus files
corpus-list:
	uv run benchmarks/corpus_manager.py list --folder benchmarks

# Generate small corpus (10MB)
corpus-10mb:
	uv run benchmarks/corpus_manager.py generate --size 10 --folder benchmarks

# Generate medium corpus (100MB)
corpus-100mb:
	uv run benchmarks/corpus_manager.py generate --size 100 --folder benchmarks

# Generate large corpus (1GB)
corpus-1gb:
	uv run benchmarks/corpus_manager.py generate --size 1000 --folder benchmarks

# ========================================
# Database Management
# ========================================

# List available benchmark databases
db-list:
	uv run benchmarks/db_manager.py list --folder benchmarks

# Generate Tantivy database from 10MB corpus
db-tantivy-10mb: release corpus-10mb
	uv run benchmarks/db_manager.py generate --corpus benchmarks/corpus_10mb.pkl --engine tantivy

# Generate FTS5 database from 10MB corpus
db-fts5-10mb: corpus-10mb
	uv run benchmarks/db_manager.py generate --corpus benchmarks/corpus_10mb.pkl --engine fts5

# Generate Tantivy database from 100MB corpus
db-tantivy-100mb: release corpus-100mb
	uv run benchmarks/db_manager.py generate --corpus benchmarks/corpus_100mb.pkl --engine tantivy

# Generate FTS5 database from 100MB corpus
db-fts5-100mb: corpus-100mb
	uv run benchmarks/db_manager.py generate --corpus benchmarks/corpus_100mb.pkl --engine fts5

# ========================================
# Benchmarking
# ========================================

# Run benchmark on Tantivy 10MB database
bench-tantivy-10mb: db-tantivy-10mb
	uv run benchmarks/benchmark.py --db benchmarks/corpus_10mb_tantivy.db --cache-sizes "1000,5000,10000,20000"

# Run benchmark on FTS5 10MB database
bench-fts5-10mb: db-fts5-10mb
	uv run benchmarks/benchmark.py --db benchmarks/corpus_10mb_fts5.db --cache-sizes "1000,5000,10000,20000"

# Run benchmark on Tantivy 100MB database
bench-tantivy-100mb: db-tantivy-100mb
	uv run benchmarks/benchmark.py --db benchmarks/corpus_100mb_tantivy.db --cache-sizes "5000,10000,20000,40000"

# Run benchmark on FTS5 100MB database
bench-fts5-100mb: db-fts5-100mb
	uv run benchmarks/benchmark.py --db benchmarks/corpus_100mb_fts5.db --cache-sizes "5000,10000,20000,40000"

# Quick end-to-end benchmark (10MB, Tantivy only)
bench-quick: release
	uv run benchmarks/corpus_manager.py generate --size 10 --folder benchmarks
	uv run benchmarks/db_manager.py generate --corpus benchmarks/corpus_10mb.pkl --engine tantivy
	uv run benchmarks/benchmark.py --db benchmarks/corpus_10mb_tantivy.db --cache-sizes "1000,5000,10000"

# Full comparison benchmark (10MB, both engines)
bench-compare-10mb: release
	uv run benchmarks/corpus_manager.py generate --size 10 --folder benchmarks
	uv run benchmarks/db_manager.py generate --corpus benchmarks/corpus_10mb.pkl --engine tantivy
	uv run benchmarks/db_manager.py generate --corpus benchmarks/corpus_10mb.pkl --engine fts5
	@echo "\n========== TANTIVY BENCHMARK =========="
	uv run benchmarks/benchmark.py --db benchmarks/corpus_10mb_tantivy.db --cache-sizes "1000,5000,10000,20000" --output benchmarks/tantivy_10mb.json
	@echo "\n========== FTS5 BENCHMARK =========="
	uv run benchmarks/benchmark.py --db benchmarks/corpus_10mb_fts5.db --cache-sizes "1000,5000,10000,20000" --output benchmarks/fts5_10mb.json

# Full comparison benchmark (100MB, both engines)
bench-compare-100mb: release
	uv run benchmarks/corpus_manager.py generate --size 100 --folder benchmarks
	uv run benchmarks/db_manager.py generate --corpus benchmarks/corpus_100mb.pkl --engine tantivy
	uv run benchmarks/db_manager.py generate --corpus benchmarks/corpus_100mb.pkl --engine fts5
	@echo "\n========== TANTIVY BENCHMARK =========="
	uv run benchmarks/benchmark.py --db benchmarks/corpus_100mb_tantivy.db --cache-sizes "5000,10000,20000,40000" --output benchmarks/tantivy_100mb.json
	@echo "\n========== FTS5 BENCHMARK =========="
	uv run benchmarks/benchmark.py --db benchmarks/corpus_100mb_fts5.db --cache-sizes "5000,10000,20000,40000" --output benchmarks/fts5_100mb.json

help:
	@echo "Available targets:"
	@echo ""
	@echo "Build & Test:"
	@echo "  build                 - Build debug version"
	@echo "  release               - Build release version"
	@echo "  test                  - Run all tests"
	@echo "  test-unit             - Run unit tests only"
	@echo "  test-integration      - Run integration tests"
	@echo "  clean                 - Clean build artifacts"
	@echo "  lint                  - Run clippy lints"
	@echo "  fmt                   - Format code"
	@echo "  check                 - Full check (fmt + lint + test)"
	@echo "  repl                  - Load extension in sqlite3 CLI"
	@echo ""
	@echo "Corpus Management:"
	@echo "  corpus-list           - List available corpus files"
	@echo "  corpus-10mb           - Generate 10MB corpus"
	@echo "  corpus-100mb          - Generate 100MB corpus"
	@echo "  corpus-1gb            - Generate 1GB corpus"
	@echo ""
	@echo "Database Management:"
	@echo "  db-list               - List available benchmark databases"
	@echo "  db-tantivy-10mb       - Generate Tantivy database (10MB)"
	@echo "  db-fts5-10mb          - Generate FTS5 database (10MB)"
	@echo "  db-tantivy-100mb      - Generate Tantivy database (100MB)"
	@echo "  db-fts5-100mb         - Generate FTS5 database (100MB)"
	@echo ""
	@echo "Benchmarking:"
	@echo "  bench-quick           - Quick benchmark (10MB, Tantivy)"
	@echo "  bench-compare-10mb    - Compare Tantivy vs FTS5 (10MB)"
	@echo "  bench-compare-100mb   - Compare Tantivy vs FTS5 (100MB)"
	@echo "  bench-tantivy-10mb    - Benchmark Tantivy (10MB)"
	@echo "  bench-fts5-10mb       - Benchmark FTS5 (10MB)"
	@echo "  bench-tantivy-100mb   - Benchmark Tantivy (100MB)"
	@echo "  bench-fts5-100mb      - Benchmark FTS5 (100MB)"
	@echo ""
	@echo "Manual Commands:"
	@echo "  uv run benchmarks/corpus_manager.py --help"
	@echo "  uv run benchmarks/db_manager.py --help"
	@echo "  uv run benchmarks/benchmark.py --help"
