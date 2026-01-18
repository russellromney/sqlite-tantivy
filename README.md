# sqlite-tantivy

A SQLite extension that provides [Tantivy](https://github.com/quickwit-oss/tantivy)-powered full-text search with an FTS5-compatible API.

## Features

- **FTS5-like syntax**: Familiar `CREATE VIRTUAL TABLE ... USING tantivy()` and `MATCH` queries
- **Tantivy-powered**: Fast, Lucene-inspired full-text search written in Rust
- **Rich query syntax**: Boolean operators, phrase search, fuzzy matching, field-scoped queries
- **BM25 ranking**: Relevance-based result ordering
- **Multiple field types**: TEXT, TAG, INTEGER, FLOAT
- **Single-file storage**: Indexes stored inside SQLite as BLOBs, works with Litestream

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

-- Insert documents
INSERT INTO articles(rowid, title, body, author) VALUES
  (1, 'Hello World', 'An introduction to full-text search', 'Alice'),
  (2, 'Rust Programming', 'Building fast systems with Rust', 'Bob');

-- Search with MATCH syntax
SELECT rowid, title FROM articles WHERE articles MATCH 'hello';
SELECT rowid, title FROM articles WHERE articles MATCH 'title:rust';
SELECT rowid, title FROM articles WHERE articles MATCH '"full-text search"';

-- Delete documents
DELETE FROM articles WHERE rowid = 1;
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

## How It Works

sqlite-tantivy stores Tantivy index segments directly in SQLite tables:

- `_tantivy_indexes` - Index metadata and schema
- `_tantivy_segments` - Binary segment data as BLOBs

This enables single-file databases that work with backup tools like Litestream, and ensures the index stays in sync with your SQLite database.

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

## Known Issues

This extension requires a patched version of [sqlite-loadable-rs](https://github.com/asg017/sqlite-loadable-rs) to support INSERT with explicit rowid. See [PR #26](https://github.com/asg017/sqlite-loadable-rs/pull/26).

## Dependencies

- [sqlite-loadable-rs](https://github.com/asg017/sqlite-loadable-rs) - SQLite extension framework
- [tantivy](https://github.com/quickwit-oss/tantivy) - Full-text search engine library

## License

Apache-2.0
