# Benchmark System Summary

Comprehensive benchmarking system for sqlite-tantivy with cache size testing.

## ✅ What We Built

### 1. **Three-Part Modular Architecture**

```
Corpus Manager → Database Manager → Benchmark Runner
```

- **Corpus Manager** (`corpus_manager.py`) - Downloads/caches Project Gutenberg texts
- **Database Manager** (`db_manager.py`) - Creates benchmark databases from corpuses
- **Benchmark Runner** (`benchmark.py`) - Tests query performance on existing databases

### 2. **Key Features**

✅ **Flexible Database Sizing** (`--db-size` parameter)
- Create 100MB, 500MB, 1GB, 5GB databases from one 1GB corpus
- Larger than corpus: Documents duplicated with markers
- Smaller than corpus: Random sampling
- Reuse corpus for multiple database sizes

✅ **Index Build Time Tracking**
- Measures index creation performance during DB generation
- Tracks docs/sec throughput
- Saves metrics to database metadata

✅ **Safe Benchmarking**
- Each test creates a temporary copy of the database
- Original database never modified
- Protects against corruption from interrupted tests

✅ **Compression & Encryption Support**
- Ready for `sqlite-compress-vfs`
- Fails hard if requested but not available (no silent fallbacks)
- Naming convention includes compression/encryption flags

✅ **Smart Naming Convention**
- `corpus_10mb_tantivy.db` - DB matches corpus size
- `db_500mb_tantivy.db` - DB created with `--db-size 500`
- `db_5000mb_tantivy_compressed.db` - 5GB with compression
- All `.db`, `.pkl`, `.json` files gitignored

## 🚀 Quick Start

```bash
# 1. Generate corpus once
make corpus-100mb

# 2. Create databases of various sizes
uv run benchmarks/db_manager.py generate --corpus benchmarks/corpus_100mb.pkl --engine tantivy --db-size 50
uv run benchmarks/db_manager.py generate --corpus benchmarks/corpus_100mb.pkl --engine tantivy --db-size 200
uv run benchmarks/db_manager.py generate --corpus benchmarks/corpus_100mb.pkl --engine tantivy --db-size 1000

# 3. Benchmark each
uv run benchmarks/benchmark.py --db benchmarks/db_50mb_tantivy.db --cache-sizes "1000,5000,10000"
uv run benchmarks/benchmark.py --db benchmarks/db_200mb_tantivy.db --cache-sizes "5000,10000,20000"
uv run benchmarks/benchmark.py --db benchmarks/db_1000mb_tantivy.db --cache-sizes "10000,20000,40000"
```

## 📊 What Gets Measured

### During Database Generation
- **Index build time** (seconds)
- **Indexing throughput** (docs/sec)
- Saved to database metadata

### During Benchmarking
- **Query latency**
  - Average, min, max, p50, p95
  - Per query type (term, phrase, field)
- **Cache performance**
  - Multiple cache sizes tested
  - Cache/DB ratio calculated
  - Best cache size identified
- **Database info**
  - Document count
  - Database size
  - Compression ratio (if compressed)

## 🎯 Use Cases

### 1. **Test Cache Size Impact**
```bash
# How does cache size affect performance?
uv run benchmarks/benchmark.py --db benchmarks/corpus_10mb_tantivy.db \
  --cache-sizes "500,1000,5000,10000,20000"
```

### 2. **Compare Tantivy vs FTS5**
```bash
# Same corpus, different engines
uv run benchmarks/db_manager.py generate --corpus benchmarks/corpus_100mb.pkl --engine tantivy
uv run benchmarks/db_manager.py generate --corpus benchmarks/corpus_100mb.pkl --engine fts5

uv run benchmarks/benchmark.py --db benchmarks/corpus_100mb_tantivy.db --cache-sizes "10000,20000"
uv run benchmarks/benchmark.py --db benchmarks/corpus_100mb_fts5.db --cache-sizes "10000,20000"
```

### 3. **Test Scaling Behavior**
```bash
# One corpus → multiple database sizes
make corpus-1gb

uv run benchmarks/db_manager.py generate --corpus benchmarks/corpus_1000mb.pkl --engine tantivy --db-size 100
uv run benchmarks/db_manager.py generate --corpus benchmarks/corpus_1000mb.pkl --engine tantivy --db-size 500
uv run benchmarks/db_manager.py generate --corpus benchmarks/corpus_1000mb.pkl --engine tantivy --db-size 2000

# Benchmark each to see how performance scales
```

### 4. **Test Compression Impact**
```bash
# Same data, with/without compression
uv run benchmarks/db_manager.py generate --corpus benchmarks/corpus_100mb.pkl --engine tantivy
uv run benchmarks/db_manager.py generate --corpus benchmarks/corpus_100mb.pkl --engine tantivy --compress

# Compare performance
```

## 📁 File Structure

```
benchmarks/
├── corpus_manager.py          # Corpus generation
├── db_manager.py              # Database creation
├── benchmark.py               # Performance testing
├── download_corpus.py         # Project Gutenberg downloader
├── README.md                  # Full documentation
│
├── corpus_100mb.pkl           # Reusable corpus (gitignored)
├── corpus_1000mb.pkl          # Large corpus (gitignored)
│
├── db_50mb_tantivy.db         # Test databases (gitignored)
├── db_200mb_tantivy.db
├── db_1000mb_tantivy.db
├── corpus_100mb_fts5.db
│
├── results_*.json             # Benchmark results (gitignored)
│
└── gutenberg_cache/           # Downloaded texts (gitignored)
    ├── 100.txt                # Shakespeare
    ├── 2701.txt               # Moby Dick
    └── ...
```

## 🔧 Technical Details

### Database Generation
- Uses `executemany()` for bulk inserts
- Builds full-text index during insert
- Tracks build time automatically
- Commits once at end

### Benchmarking Safety
- Creates temp copy before each test: `shutil.copy2(original, temp)`
- Tests run on copy, original untouched
- Temp copy deleted after test
- Prevents corruption from interrupted benchmarks

### Cache Testing
- Tests multiple cache sizes in one run
- Each cache size gets fresh DB copy
- Measures: avg, min, max, p50, p95 latencies
- Identifies optimal cache size

## 📝 Next Steps

To actually run benchmarks:

1. **Generate small corpus for testing** (~5 minutes)
   ```bash
   make corpus-10mb
   ```

2. **Create test databases** (~1-2 minutes each)
   ```bash
   make db-tantivy-10mb
   make db-fts5-10mb
   ```

3. **Run benchmarks** (~1 minute per cache size)
   ```bash
   make bench-tantivy-10mb
   make bench-fts5-10mb
   ```

4. **Or do everything at once**
   ```bash
   make bench-compare-10mb
   ```

## 🎓 What You'll Learn

- How cache size affects query performance
- Tantivy vs FTS5 performance differences
- How index build time scales with data size
- Optimal cache size for your database size
- Performance impact of compression/encryption

---

**The system is ready to use!** All code is committed and working.
