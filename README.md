# sqlite-tantivy

A SQLite extension that provides [Tantivy](https://github.com/quickwit-oss/tantivy)-powered full-text search with an FTS5-compatible API.

## Features

- **FTS5-like syntax**: Familiar `CREATE VIRTUAL TABLE ... USING tantivy()` and `MATCH` queries
- **Tantivy-powered**: Fast, Lucene-inspired full-text search written in Rust
- **Rich query syntax**: Boolean operators, phrase search, fuzzy matching, field-scoped queries
- **BM25 ranking**: Relevance-based result ordering
- **Multiple field types**: TEXT, TAG, INTEGER, FLOAT
- **Ecosystem compatible**: Indexes stored in SQLite (planned), works with Litestream

## Current Status

**Phase 1 Complete (Foundation)**:
- Project structure and build system
- SqliteDirectory for blob storage (not yet integrated)
- Schema parser for virtual table definitions
- Query parser supporting: terms, phrases, boolean (AND/OR/NOT), fuzzy (`~`), prefix (`*`), field-scoped

**Work in Progress**:
- Virtual table implementation (read-only search working)
- Write support (INSERT/UPDATE/DELETE)
- SqliteDirectory integration for single-file databases

## Usage

```sql
-- Load the extension
.load ./target/release/libsqlite_tantivy

-- Create a virtual table with text fields
CREATE VIRTUAL TABLE articles USING tantivy(
  title TEXT,
  body TEXT,
  author TEXT
);

-- Search (once INSERT support is complete)
SELECT * FROM articles WHERE articles MATCH 'hello world';
SELECT * FROM articles WHERE articles MATCH 'title:rust AND body:performance';
SELECT * FROM articles WHERE articles MATCH '"exact phrase"';
SELECT * FROM articles WHERE articles MATCH 'helo~1';  -- fuzzy search
```

## Query Syntax

| Syntax | Description |
|--------|-------------|
| `hello world` | Both terms must match (implicit AND) |
| `hello OR world` | Either term matches |
| `NOT hello` or `-hello` | Exclude term |
| `"exact phrase"` | Phrase match |
| `hel*` | Prefix match |
| `helo~1` | Fuzzy match (edit distance 1) |
| `title:hello` | Search specific field |
| `title:"hello world"` | Phrase in specific field |

## Building

```bash
# Debug build
make build

# Release build
make release

# Run tests
make test

# Load in SQLite CLI
make repl
```

## Requirements

- Rust 1.70+
- SQLite with extension loading enabled

## Architecture

```
sqlite-tantivy/
├── src/
│   ├── lib.rs        # Extension entry point
│   ├── vtab.rs       # Virtual table implementation
│   ├── directory.rs  # SQLite blob storage for Tantivy
│   ├── schema.rs     # CREATE TABLE parser
│   ├── query.rs      # MATCH expression parser
│   └── error.rs      # Error types
└── tests/
```

## Dependencies

- [sqlite-loadable-rs](https://github.com/asg017/sqlite-loadable-rs) - SQLite extension framework
- [tantivy](https://github.com/quickwit-oss/tantivy) - Full-text search engine library

## License

MIT
