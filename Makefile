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

help:
	@echo "Available targets:"
	@echo "  build           - Build debug version"
	@echo "  release         - Build release version"
	@echo "  test            - Run all tests"
	@echo "  test-unit       - Run unit tests only"
	@echo "  test-integration - Run integration tests"
	@echo "  clean           - Clean build artifacts"
	@echo "  lint            - Run clippy lints"
	@echo "  fmt             - Format code"
	@echo "  check           - Full check (fmt + lint + test)"
	@echo "  repl            - Load extension in sqlite3 CLI"
