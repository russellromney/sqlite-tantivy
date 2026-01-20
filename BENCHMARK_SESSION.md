# Benchmark Session Plan

Run comprehensive benchmarks comparing Tantivy vs FTS5 across various database sizes and cache configurations.

## Session Goal

Generate performance data for:
- 10MB, 100MB, 500MB, 1GB databases
- Tantivy vs FTS5 comparison
- Various cache sizes (1K, 5K, 10K, 20K, 40K pages)
- Index build time metrics
- Query latency at different percentiles

## Execution Plan

### Phase 1: Corpus Generation (~5-10 minutes)
```bash
cd sqlite-tantivy

# Generate reusable corpus (download texts once)
make corpus-100mb  # Downloads ~100MB of Project Gutenberg texts
```

### Phase 2: Database Generation (~30-60 minutes total)

Create databases of various sizes from the single corpus:

```bash
# 10MB databases
uv run benchmarks/db_manager.py generate --corpus benchmarks/corpus_100mb.pkl --engine tantivy --db-size 10
uv run benchmarks/db_manager.py generate --corpus benchmarks/corpus_100mb.pkl --engine fts5 --db-size 10

# 50MB databases
uv run benchmarks/db_manager.py generate --corpus benchmarks/corpus_100mb.pkl --engine tantivy --db-size 50
uv run benchmarks/db_manager.py generate --corpus benchmarks/corpus_100mb.pkl --engine fts5 --db-size 50

# 100MB databases (full corpus)
uv run benchmarks/db_manager.py generate --corpus benchmarks/corpus_100mb.pkl --engine tantivy
uv run benchmarks/db_manager.py generate --corpus benchmarks/corpus_100mb.pkl --engine fts5

# 500MB databases (duplicated)
uv run benchmarks/db_manager.py generate --corpus benchmarks/corpus_100mb.pkl --engine tantivy --db-size 500
uv run benchmarks/db_manager.py generate --corpus benchmarks/corpus_100mb.pkl --engine fts5 --db-size 500
```

**Note:** Tantivy will be slower than FTS5 for indexing. This is expected and will be measured.

### Phase 3: Benchmarking (~20-30 minutes total)

Run cache size tests on each database:

```bash
# 10MB databases
uv run benchmarks/benchmark.py --db benchmarks/db_10mb_tantivy.db \
  --cache-sizes "1000,5000,10000,20000" \
  --queries 200 \
  --output results_10mb_tantivy.json

uv run benchmarks/benchmark.py --db benchmarks/db_10mb_fts5.db \
  --cache-sizes "1000,5000,10000,20000" \
  --queries 200 \
  --output results_10mb_fts5.json

# 50MB databases
uv run benchmarks/benchmark.py --db benchmarks/db_50mb_tantivy.db \
  --cache-sizes "2000,10000,20000,40000" \
  --queries 200 \
  --output results_50mb_tantivy.json

uv run benchmarks/benchmark.py --db benchmarks/db_50mb_fts5.db \
  --cache-sizes "2000,10000,20000,40000" \
  --queries 200 \
  --output results_50mb_fts5.json

# 100MB databases
uv run benchmarks/benchmark.py --db benchmarks/corpus_100mb_tantivy.db \
  --cache-sizes "5000,10000,20000,40000" \
  --queries 200 \
  --output results_100mb_tantivy.json

uv run benchmarks/benchmark.py --db benchmarks/corpus_100mb_fts5.db \
  --cache-sizes "5000,10000,20000,40000" \
  --queries 200 \
  --output results_100mb_fts5.json

# 500MB databases
uv run benchmarks/benchmark.py --db benchmarks/db_500mb_tantivy.db \
  --cache-sizes "10000,20000,40000,80000" \
  --queries 200 \
  --output results_500mb_tantivy.json

uv run benchmarks/benchmark.py --db benchmarks/db_500mb_fts5.db \
  --cache-sizes "10000,20000,40000,80000" \
  --queries 200 \
  --output results_500mb_fts5.json
```

### Phase 4: Results Analysis

View index build performance:
```bash
# Check index build times for each database
uv run benchmarks/db_manager.py info benchmarks/db_10mb_tantivy.db
uv run benchmarks/db_manager.py info benchmarks/db_10mb_fts5.db
uv run benchmarks/db_manager.py info benchmarks/db_50mb_tantivy.db
uv run benchmarks/db_manager.py info benchmarks/db_50mb_fts5.db
uv run benchmarks/db_manager.py info benchmarks/corpus_100mb_tantivy.db
uv run benchmarks/db_manager.py info benchmarks/corpus_100mb_fts5.db
uv run benchmarks/db_manager.py info benchmarks/db_500mb_tantivy.db
uv run benchmarks/db_manager.py info benchmarks/db_500mb_fts5.db
```

Results are also in JSON files (`benchmarks/results_*.json`) for further analysis.

## Expected Insights

After running these benchmarks, you'll have data on:

1. **Index Build Performance**
   - Tantivy vs FTS5 indexing speed
   - How indexing scales with database size
   - Docs/sec throughput at different sizes

2. **Query Performance**
   - Tantivy vs FTS5 query latency
   - Impact of cache size on performance
   - Optimal cache size for each database size
   - How performance scales with database size

3. **Cache Efficiency**
   - Cache/DB size ratio sweet spot
   - Diminishing returns threshold
   - Memory vs performance tradeoffs

4. **Database Characteristics**
   - Compression ratios
   - Index size overhead
   - Storage efficiency comparison

## Quick Start (Minimal Test)

If you want a faster test with just one database size:

```bash
cd sqlite-tantivy

# One command does it all (10MB databases)
make bench-compare-10mb
```

This will:
1. Generate 10MB corpus
2. Create Tantivy and FTS5 databases
3. Run benchmarks with 4 cache sizes
4. Output comparison results

## Troubleshooting

- **Slow index builds**: Tantivy is slower than FTS5 for indexing. Run in background if needed.
- **Out of memory**: Reduce database sizes or cache sizes
- **Disk space**: Each 100MB corpus → ~120MB total for both DBs

## Files Generated

After completion, you'll have:

```
benchmarks/
├── corpus_100mb.pkl           # Reusable corpus
├── db_10mb_tantivy.db         # Test databases
├── db_10mb_fts5.db
├── db_50mb_tantivy.db
├── db_50mb_fts5.db
├── corpus_100mb_tantivy.db
├── corpus_100mb_fts5.db
├── db_500mb_tantivy.db
├── db_500mb_fts5.db
├── results_10mb_tantivy.json  # Benchmark results
├── results_10mb_fts5.json
├── results_50mb_tantivy.json
├── results_50mb_fts5.json
├── results_100mb_tantivy.json
├── results_100mb_fts5.json
├── results_500mb_tantivy.json
├── results_500mb_fts5.json
└── gutenberg_cache/           # Downloaded texts (cached)
    ├── 100.txt
    ├── 2701.txt
    └── ...
```

All gitignored automatically!
