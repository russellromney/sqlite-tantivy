# Benchmarking sqlite-tantivy

Comprehensive benchmark system for comparing sqlite-tantivy performance against SQLite FTS5 with various page cache sizes.

## Quick Start

```bash
# 1. Generate a 10MB corpus from Project Gutenberg
make corpus-10mb

# 2. Create benchmark databases (both Tantivy and FTS5)
make db-tantivy-10mb
make db-fts5-10mb

# 3. Run benchmarks
make bench-tantivy-10mb
make bench-fts5-10mb

# Or run everything in one command:
make bench-compare-10mb
```

## Architecture

The benchmark system has three independent components:

### 1. Corpus Manager ([corpus_manager.py](corpus_manager.py))

Downloads and caches public domain texts from Project Gutenberg.

**Commands:**
```bash
# List available corpuses
make corpus-list
# or manually:
uv run benchmarks/corpus_manager.py list

# Generate corpuses of different sizes
make corpus-10mb      # 10MB corpus (~1,000 documents)
make corpus-100mb     # 100MB corpus (~10,000 documents)
make corpus-1gb       # 1GB corpus (~100,000 documents)

# Custom size
uv run benchmarks/corpus_manager.py generate --size 50 --folder benchmarks

# Show corpus info
uv run benchmarks/corpus_manager.py info benchmarks/corpus_10mb.pkl
```

**Output:** Corpus files saved as `corpus_{size}mb.pkl` in the benchmarks folder.

### 2. Database Manager ([db_manager.py](db_manager.py))

Creates benchmark databases from corpus files.

**Commands:**
```bash
# List existing databases
make db-list
# or manually:
uv run benchmarks/db_manager.py list

# Generate databases from corpus
make db-tantivy-10mb   # Create Tantivy database
make db-fts5-10mb      # Create FTS5 database

# Custom database generation (same size as corpus)
uv run benchmarks/db_manager.py generate \
  --corpus benchmarks/corpus_10mb.pkl \
  --engine tantivy \
  --output my_custom.db

# Generate database of specific size (can be larger or smaller than corpus!)
uv run benchmarks/db_manager.py generate \
  --corpus benchmarks/corpus_100mb.pkl \
  --engine tantivy \
  --db-size 500 \
  --output db_500mb_tantivy.db

# Create small database from large corpus (sampling)
uv run benchmarks/db_manager.py generate \
  --corpus benchmarks/corpus_1000mb.pkl \
  --engine tantivy \
  --db-size 50

# Create large database from small corpus (duplication)
uv run benchmarks/db_manager.py generate \
  --corpus benchmarks/corpus_100mb.pkl \
  --engine tantivy \
  --db-size 5000

# With compression (requires sqlite-compress-vfs)
uv run benchmarks/db_manager.py generate \
  --corpus benchmarks/corpus_10mb.pkl \
  --engine tantivy \
  --compress

# With encryption (requires sqlite-compress-vfs)
uv run benchmarks/db_manager.py generate \
  --corpus benchmarks/corpus_10mb.pkl \
  --engine tantivy \
  --encrypt

# Show database info
uv run benchmarks/db_manager.py info benchmarks/corpus_10mb_tantivy.db
```

**Database Naming Convention:**
- Without `--db-size`: `corpus{size}mb_{engine}[_compressed][_encrypted].db`
- With `--db-size`: `db_{size}mb_{engine}[_compressed][_encrypted].db`

**Examples:**
- `corpus_10mb_tantivy.db` - Database matches corpus size
- `db_500mb_tantivy.db` - Database created with `--db-size 500`
- `corpus_100mb_fts5.db` - FTS5 database from 100MB corpus
- `db_5000mb_tantivy_compressed.db` - 5GB database with compression
- `corpus_50mb_tantivy_encrypted.db` - 50MB with encryption

**Database Size Control:**

You can create databases of **any size** from a single corpus using `--db-size`:

- **Larger than corpus**: Documents are duplicated (with markers like "Copy 1", "Copy 2")
- **Smaller than corpus**: Documents are randomly sampled
- **Same as corpus**: No `--db-size` needed, uses all documents

This allows you to:
- Create a 1GB corpus once
- Generate databases at 100MB, 500MB, 1GB, 5GB, 10GB for testing
- Test how performance scales with database size
- Reuse the same corpus for multiple benchmark scenarios

**Note:** All `.db` files are automatically git-ignored.

### 3. Benchmark Runner ([benchmark.py](benchmark.py))

Runs performance benchmarks on existing databases.

**Commands:**
```bash
# Run benchmarks with various cache sizes
make bench-tantivy-10mb
make bench-fts5-10mb

# Custom benchmark
uv run benchmarks/benchmark.py \
  --db benchmarks/corpus_10mb_tantivy.db \
  --cache-sizes "1000,5000,10000,20000" \
  --queries 200 \
  --output results.json
```

**Important:**
- The database file must exist before running the benchmark. If it doesn't exist, the benchmark will fail and show available databases.
- **Benchmarks work on a copy** - Each test creates a temporary copy of your database, so the original is never modified or corrupted.
- **Indexes are pre-built** - All indexes are built during database generation, not during benchmarking. This ensures consistent benchmark results.

## Understanding Page Cache Sizes

SQLite uses an in-memory page cache to improve performance. Cache size is measured in pages (typically 4KB each):

| Pages | Memory | Use Case |
|-------|--------|----------|
| 1,000 | ~4 MB | Minimal cache, shows worst-case performance |
| 5,000 | ~20 MB | Small cache, typical for constrained environments |
| 10,000 | ~40 MB | Medium cache, good for most applications |
| 20,000 | ~80 MB | Large cache, can hold entire small databases in memory |
| 40,000 | ~160 MB | Very large cache, for 100MB+ databases |

**Key Insight:** Performance often plateaus when cache size >= database size.

## Common Workflows

### Quick Test (Fastest)

```bash
make bench-quick
```

This generates a 10MB corpus, creates a Tantivy database, and benchmarks it with 3 cache sizes.

### Compare Tantivy vs FTS5 (10MB)

```bash
make bench-compare-10mb
```

Generates corpus, creates both Tantivy and FTS5 databases, and benchmarks both.

### Large-Scale Benchmark (100MB)

```bash
make bench-compare-100mb
```

Full comparison on 100MB corpus. Takes longer but shows performance at scale.

### Custom Workflow

```bash
# 1. Create corpus once
uv run benchmarks/corpus_manager.py generate --size 50 --folder benchmarks

# 2. Create multiple database variants
uv run benchmarks/db_manager.py generate \
  --corpus benchmarks/corpus_50mb.pkl \
  --engine tantivy

uv run benchmarks/db_manager.py generate \
  --corpus benchmarks/corpus_50mb.pkl \
  --engine fts5

# 3. Benchmark each with different cache sizes
uv run benchmarks/benchmark.py \
  --db benchmarks/corpus_50mb_tantivy.db \
  --cache-sizes "2000,10000,20000,40000" \
  --queries 200 \
  --output tantivy_50mb_results.json

uv run benchmarks/benchmark.py \
  --db benchmarks/corpus_50mb_fts5.db \
  --cache-sizes "2000,10000,20000,40000" \
  --queries 200 \
  --output fts5_50mb_results.json
```

## Index Build Time

Database generation measures **index creation performance**:

```bash
uv run benchmarks/db_manager.py generate --corpus benchmarks/corpus_100mb.pkl --engine tantivy

# Output includes:
#   Inserting 10,000 documents and building index...
#   Done! Index built in 45.23s (221 docs/sec)
```

**Metrics tracked:**
- Total index build time (seconds)
- Indexing throughput (docs/sec)
- Saved in database metadata for reference

This helps you understand how index build time scales with database size.

## Benchmark Output

The benchmark produces detailed performance metrics:

```
BENCHMARK RESULTS
==================================================================================
Database: benchmarks/corpus_10mb_tantivy.db
Engine: tantivy
Documents: 1,234
Corpus size: 10.52 MB
Database size: 12.34 MB

Metric                               |   1000p ( 4MB) |   5000p (20MB) |  10000p (40MB) |  20000p (80MB)
-----------------------------------------------------------------------------------------------------
Query Performance
  Term query avg (ms)                |           2.34 |           1.98 |           1.76 |           1.72
  Term query p50 (ms)                |           2.12 |           1.85 |           1.65 |           1.60
  Term query p95 (ms)                |           4.56 |           3.87 |           3.21 |           3.15
  Phrase query avg (ms)              |           3.45 |           2.89 |           2.45 |           2.40
  Phrase query p95 (ms)              |           6.78 |           5.43 |           4.32 |           4.21
  Field query avg (ms)               |           1.98 |           1.65 |           1.43 |           1.39

Database Info
  DB size (MB)                       |          12.34 |          12.34 |          12.34 |          12.34
  Cache/DB ratio                     |           0.32x |           1.62x |           3.24x |           6.48x

Insights:
  Best cache size: 10000 pages (40MB)
  Speedup vs worst: 1.33x faster
  Cache size 10000 pages (40MB) fully covers database (12MB)
```

## Data Sources

The corpus is built from public domain texts including:

- **Shakespeare**: Complete Works (Sonnets, Plays, Poems)
- **Homer**: The Iliad & The Odyssey
- **Bible**: King James Version
- **Tolstoy**: War and Peace
- **Melville**: Moby Dick
- **Austen**: Pride and Prejudice, Complete Works
- **Dickens**: A Tale of Two Cities, David Copperfield
- **Dostoevsky**: Crime and Punishment, The Brothers Karamazov
- **Plato**: The Republic
- **Joyce**: Ulysses
- **Darwin**: On the Origin of Species

Texts are downloaded from [Project Gutenberg](https://www.gutenberg.org/) and cached locally in `benchmarks/gutenberg_cache/`.

## File Organization

```
benchmarks/
├── corpus_manager.py       # Corpus generation and management
├── db_manager.py           # Database creation and management
├── benchmark.py            # Benchmark execution
├── download_corpus.py      # Project Gutenberg downloader
├── README.md               # This file
│
├── corpus_10mb.pkl         # Generated corpus files (gitignored)
├── corpus_100mb.pkl
├── corpus_1gb.pkl
│
├── corpus_*_tantivy.db     # Generated databases (gitignored)
├── corpus_*_fts5.db
│
├── *.json                  # Benchmark results (gitignored)
│
└── gutenberg_cache/        # Cached Project Gutenberg texts (gitignored)
    ├── 100.txt             # Shakespeare Complete Works
    ├── 2701.txt            # Moby Dick
    └── ...
```

## Compression & Encryption

The system supports compression and encryption via `sqlite-compress-vfs`:

```bash
# Create compressed database
uv run benchmarks/db_manager.py generate \
  --corpus benchmarks/corpus_10mb.pkl \
  --engine tantivy \
  --compress

# Creates: corpus_10mb_tantivy_compressed.db

# Create encrypted database
uv run benchmarks/db_manager.py generate \
  --corpus benchmarks/corpus_10mb.pkl \
  --engine tantivy \
  --encrypt

# Creates: corpus_10mb_tantivy_encrypted.db

# Both compression and encryption
uv run benchmarks/db_manager.py generate \
  --corpus benchmarks/corpus_10mb.pkl \
  --engine tantivy \
  --compress \
  --encrypt

# Creates: corpus_10mb_tantivy_compressed_encrypted.db
```

**Requirements:**
- `../sqlite-compress-vfs` must exist and be built
- **The command will fail** if compression/encryption is requested but sqlite-compress-vfs is not available
- This is intentional - no silent fallbacks

## Troubleshooting

### "Database does not exist" error

The benchmark requires a pre-existing database file. Generate it first:

```bash
make db-tantivy-10mb
# then
make bench-tantivy-10mb
```

### "Corpus does not exist" error

Generate the corpus before creating databases:

```bash
make corpus-10mb
# then
make db-tantivy-10mb
```

### Slow downloads on first run

Project Gutenberg texts are downloaded on first corpus generation. Subsequent runs use cached files from `benchmarks/gutenberg_cache/`.

To pre-download:
```bash
uv run benchmarks/download_corpus.py 10  # Download 10MB worth of texts
```

### Extension not found

Make sure to build the Tantivy extension first:
```bash
make release
```

## Advanced Usage

### Benchmark an Existing Custom Database

If you have a custom SQLite FTS5 or Tantivy database:

```bash
# Just run the benchmark directly
uv run benchmarks/benchmark.py \
  --db /path/to/your/database.db \
  --cache-sizes "1000,5000,10000" \
  --queries 100
```

The benchmark will work with any database that has an `articles` table supporting `MATCH` queries.

### Generate Multiple Corpuses

```bash
# Create various sizes for testing
make corpus-10mb
make corpus-100mb
make corpus-1gb

# List all
make corpus-list
```

### Benchmark Different Configurations

```bash
# Create databases with same corpus
CORPUS=benchmarks/corpus_100mb.pkl

uv run benchmarks/db_manager.py generate --corpus $CORPUS --engine tantivy
uv run benchmarks/db_manager.py generate --corpus $CORPUS --engine fts5

# Benchmark each with specific cache sizes
uv run benchmarks/benchmark.py --db benchmarks/corpus_100mb_tantivy.db \
  --cache-sizes "10000,20000,40000" --output tantivy.json

uv run benchmarks/benchmark.py --db benchmarks/corpus_100mb_fts5.db \
  --cache-sizes "10000,20000,40000" --output fts5.json
```

## Performance Tips

1. **Cache size matters**: Test cache sizes around your database size
2. **Reuse corpuses**: Generate once, create multiple databases
3. **Compare like-for-like**: Use same corpus for Tantivy vs FTS5 comparison
4. **Run multiple times**: Average results from 3+ runs for accuracy
5. **Watch cache/DB ratio**: Performance typically plateaus at 1x ratio

## Summary

The modular design allows you to:

- ✅ Generate corpuses once, reuse many times
- ✅ Create multiple database variants (Tantivy, FTS5, compressed, encrypted)
- ✅ Benchmark existing databases without regenerating data
- ✅ Compare configurations accurately using identical datasets
- ✅ Version control workflow (corpus generation is reproducible)
- ✅ Gitignore generated data files (small repo size)

The naming convention in database files makes it easy to identify configurations at a glance.
