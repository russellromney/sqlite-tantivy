# Changelog

All notable changes to this project will be documented in this file.

## [0.2.0] - 2025-01-20

### Added
- `tantivy_flush('tablename')` scalar function for explicit index commits
- Separate segment database (`<dbname>-tantivy`) to avoid SQLite locking conflicts
- Per-connection table registry for proper isolation
- `sqlite3ext_open_v2`, `sqlite3ext_close`, `sqlite3ext_db_filename` to local sqlite-loadable fork

### Changed
- Documents are now buffered until `tantivy_flush()` is called (high-performance batch inserts)
- Segment storage moved from main database to companion `-tantivy` file
- Insert performance improved to 200,000+ docs/sec (was ~10 docs/sec with hex encoding)

### Fixed
- O(n²) hex encoding for BLOB data replaced with proper parameter binding
- SQLite locking deadlocks during Tantivy commit operations
- Connection close deadlocks (using `mem::forget` for Tantivy components)

### Known Issues
- Query terms are not stemmed (use base forms like "program" instead of "programming")
- Integration tests must run with `--test-threads=1` due to shared-cache race conditions

## [0.1.0] - 2025-01-15

### Added
- Initial implementation of sqlite-tantivy extension
- Virtual table with FTS5-like syntax (`CREATE VIRTUAL TABLE ... USING tantivy()`)
- Support for TEXT, TAG, INTEGER, FLOAT field types
- Query syntax: boolean operators, phrase search, fuzzy matching, field-scoped queries
- BM25 ranking for search results
- SQLite blob storage for Tantivy segments (SqliteDirectory)
- `tantivy_version()` scalar function
