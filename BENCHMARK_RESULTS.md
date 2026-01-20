# sqlite-tantivy Benchmark Results

**Date:** January 20, 2026
**System:** macOS (Darwin 23.1.0), Apple Silicon
**Test:** Concurrent search performance (4 threads, 10s duration, exact queries)

## Test Configuration

### Databases
- **Tantivy**: 155,487 documents, 1.4 GB index
- **FTS5**: 156,373 documents, 1.63 GB database
- **Data**: Project Gutenberg literary texts (~1GB corpus)

### Cache Sizes Tested
- 0.05 (5% of logical DB size)
- 0.10 (10% of logical DB size)
- 0.15 (15% of logical DB size)
- 0.20 (20% of logical DB size)
- 0.25 (25% of logical DB size)

## Results Summary

### Tantivy Performance

| Cache Size | Throughput (q/s) | P50 Latency (µs) | P95 Latency (µs) | P99 Latency (µs) |
|------------|------------------|------------------|------------------|------------------|
| 0.05       | 82,913           | 9                | 26               | 60               |
| 0.10       | 86,495           | 9                | 24               | 57               |
| 0.15       | 86,243           | 9                | 26               | 58               |
| 0.20       | 85,422           | 9                | 25               | 59               |
| 0.25       | 86,643           | 9                | 25               | 57               |

**Average**: ~85,543 queries/sec, P50: 9µs, P95: 25µs, P99: 58µs

### FTS5 Performance

| Cache Size | Throughput (q/s) | P50 Latency (µs) | P95 Latency (µs) | P99 Latency (µs) |
|------------|------------------|------------------|------------------|------------------|
| 0.05       | 3,176            | 302              | 538              | 634              |
| 0.10       | 3,217            | 298              | 532              | 628              |
| 0.15       | 3,234            | 297              | 529              | 625              |
| 0.20       | 3,223            | 297              | 532              | 630              |
| 0.25       | 3,105            | 303              | 551              | 701              |

**Average**: ~3,191 queries/sec, P50: 299µs, P95: 536µs, P99: 644µs

## Key Findings

### Performance Comparison
- **Throughput**: Tantivy is **26.8x faster** (85,543 vs 3,191 queries/sec)
- **P50 Latency**: Tantivy is **33.2x faster** (9µs vs 299µs)
- **P95 Latency**: Tantivy is **21.4x faster** (25µs vs 536µs)
- **P99 Latency**: Tantivy is **11.1x faster** (58µs vs 644µs)

### Storage Efficiency
- **Tantivy Index**: 1.4 GB (including 24KB SQLite DB)
- **FTS5 Database**: 1.63 GB
- **Advantage**: Tantivy uses **14% less storage**

### Cache Sensitivity
- **Tantivy**: Minimal variance across cache sizes (82.9k - 86.6k q/s, 4.3% range)
- **FTS5**: Minimal variance across cache sizes (3.1k - 3.2k q/s, 4.0% range)
- **Conclusion**: Both engines show stable performance across tested cache sizes

### Insert Performance
From database creation logs:
- **Tantivy**: 2,101 docs/sec (155,487 docs in 74.01s)
- **FTS5**: Data from creation (156,373 docs)

## Interpretation

### When to Use Tantivy
1. **High query volume**: Applications needing 50k+ queries/sec
2. **Low latency requirements**: Sub-10µs query response times
3. **Storage constrained**: Smaller index footprint
4. **Advanced search**: Complex queries, fuzzy matching, faceting

### When to Use FTS5
1. **Embedded scenarios**: No external dependencies required
2. **Simple full-text search**: Basic MATCH queries
3. **Transactional guarantees**: Full ACID within SQLite
4. **Lower complexity**: Simpler deployment and operation

## Test Commands

```bash
# Build extension
cargo build --release

# Create 1GB databases
uv run python benchmarks/db_manager.py generate \
  --corpus corpus_5000mb --engine tantivy --db-size 1000 \
  --output db_1gb_tantivy.db

uv run python benchmarks/db_manager.py generate \
  --corpus corpus_5000mb --engine fts5 --db-size 1000 \
  --output db_1gb_fts5.db

# Run benchmarks
./target/release/tantivy-bench benchmarks/db_1gb_tantivy.db tantivy \
  --duration 10 --threads 4 --cache 0.10 --query exact

./target/release/tantivy-bench benchmarks/db_1gb_fts5.db fts5 \
  --duration 10 --threads 4 --cache 0.10 --query exact
```

## Critical Limitation: Two-File Architecture

**⚠️ IMPORTANT**: Unlike FTS5, Tantivy uses a **two-file architecture** that breaks SQLite's single-file promise:

| Engine | Files | Total Size | Single-File? |
|--------|-------|------------|--------------|
| **FTS5** | 1 file: `db.db` (1.6GB) | 1.6 GB | ✅ YES |
| **Tantivy** | 2 files: `db.db` (24KB) + `db.db-tantivy` (1.4GB) | 1.4 GB | ❌ NO |

### What This Breaks

1. **❌ Portability**: Can't just copy the .db file - both files required
2. **❌ Simple backups**: Must backup both files atomically
3. **❌ VACUUM**: Doesn't reclaim index space
4. **❌ Atomic transactions**: Two separate databases = two transaction logs
5. **❌ Tool compatibility**: SQLite tools expect single-file databases

### Why It Matters

The separate index file fundamentally undermines the **"SQLite ecosystem" benefit** claimed for this extension. While FTS5 provides true single-file portability, Tantivy requires managing two files with careful coordination.

## Trade-off Analysis

### Choose Tantivy When:
- ✅ Performance is paramount (27x faster)
- ✅ You can manage two-file complexity
- ✅ You need advanced search features (fuzzy, faceting)
- ⚠️ You accept losing single-file simplicity

### Choose FTS5 When:
- ✅ Single-file database is critical
- ✅ Simple deployment/backup is required
- ✅ Ecosystem tooling compatibility matters
- ✅ 3,000 queries/sec is sufficient
- ✅ You want true SQLite ACID guarantees

## Conclusions

sqlite-tantivy demonstrates exceptional **performance** for full-text search:

1. **27x higher throughput** than SQLite FTS5
2. **33x lower median latency** (9µs vs 299µs)
3. **14% smaller total storage**
4. **Consistent performance** across cache configurations

**However**, the two-file architecture is a **critical limitation** that breaks SQLite's core value proposition. The performance gains come at the cost of operational simplicity and ecosystem compatibility.

**Recommendation**: Consider whether the 27x performance improvement justifies the loss of single-file simplicity for your use case. For many applications, FTS5's 3,000 queries/sec may be sufficient while maintaining SQLite's elegant simplicity.
