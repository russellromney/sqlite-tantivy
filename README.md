# sqlite-tantivy

A SQLite extension that provides [Tantivy](https://github.com/quickwit-oss/tantivy)-powered full-text search with an FTS5-compatible API.

## Features

- **FTS5-like syntax**: Familiar `CREATE VIRTUAL TABLE ... USING tantivy()` and `MATCH` queries
- **Tantivy-powered**: Fast, Lucene-inspired full-text search written in Rust
- **High performance**: 200,000+ document inserts per second
- **Rich query syntax**: Boolean operators, phrase search, fuzzy matching, field-scoped queries
- **BM25 ranking**: Relevance-based result ordering
- **Multiple field types**: TEXT, TAG, INTEGER, FLOAT
- **SQLite-based storage**: Indexes stored as BLOBs in a companion database file

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

-- Flush to make documents searchable
SELECT tantivy_flush('articles');

-- Search with MATCH syntax
SELECT rowid, title FROM articles WHERE articles MATCH 'hello';
SELECT rowid, title FROM articles WHERE articles MATCH 'title:rust';
SELECT rowid, title FROM articles WHERE articles MATCH '"full-text search"';

-- Delete documents
DELETE FROM articles WHERE rowid = 1;
```

## Important: Flush Before Querying

Documents are buffered in memory until explicitly flushed. Call `tantivy_flush('tablename')` to:
1. Commit pending documents to the index
2. Make them visible to search queries

```sql
-- Insert many documents quickly
INSERT INTO docs(rowid, content) VALUES (1, 'First document');
INSERT INTO docs(rowid, content) VALUES (2, 'Second document');
-- ... more inserts ...

-- Flush to make searchable (typically 10-25ms for thousands of docs)
SELECT tantivy_flush('docs');

-- Now queries will find the documents
SELECT * FROM docs WHERE docs MATCH 'document';
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

sqlite-tantivy stores Tantivy index data in SQLite:

- **Main database** (`mydb.db`): Contains `_tantivy_indexes` table with index metadata
- **Segment database** (`mydb.db-tantivy`): Contains `_tantivy_segments` table with binary segment data as BLOBs

The separate segment database avoids SQLite locking conflicts during Tantivy commit operations, enabling high-performance writes while maintaining data integrity.

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

## Known Limitations

1. **Explicit flush required**: Documents must be flushed with `tantivy_flush()` before they appear in search results. This is by design for high insert performance.

2. **Query stemming**: The default `en_stem` tokenizer stems indexed text (e.g., "programming" → "program"), but query terms are not automatically stemmed. Use base forms or prefix queries:
   ```sql
   -- Instead of: WHERE docs MATCH 'programming'
   -- Use: WHERE docs MATCH 'program'
   -- Or:  WHERE docs MATCH 'program*'
   ```

3. **Two database files**: Index segments are stored in a separate `<dbname>-tantivy` file. Both files must be backed up together.

4. **Local fork of sqlite-loadable-rs**: This extension uses a patched version of [sqlite-loadable-rs](https://github.com/asg017/sqlite-loadable-rs) to support INSERT with explicit rowid and additional SQLite API functions.

## Dependencies

- [sqlite-loadable-rs](https://github.com/asg017/sqlite-loadable-rs) - SQLite extension framework
- [tantivy](https://github.com/quickwit-oss/tantivy) - Full-text search engine library

## License

Apache-2.0
