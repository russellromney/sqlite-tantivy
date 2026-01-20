#!/bin/bash
# Quick test of the benchmark framework with small 5MB databases

set -e

cd "$(dirname "$0")/.."

echo "=== Quick Benchmark Test ==="
echo

# Check if corpus exists
if [ ! -d "benchmarks/corpus_5000mb" ]; then
    echo "ERROR: Corpus not found at benchmarks/corpus_5000mb"
    echo "Generate it with: python3 benchmarks/corpus_manager.py generate --size 5000"
    exit 1
fi

# Build extension
echo "1. Building Tantivy extension..."
cargo build --release
echo "✓ Extension built"
echo

# Build benchmark binary
echo "2. Building benchmark binary..."
cargo build --release --bin tantivy-bench
echo "✓ Benchmark binary built"
echo

# Generate test databases (5MB each)
echo "3. Generating 5MB test databases..."

for ENGINE in tantivy fts5; do
    DB="benchmarks/db_5mb_${ENGINE}.db"

    if [ -f "$DB" ]; then
        echo "  Skipping $ENGINE (already exists)"
    else
        echo "  Generating $ENGINE database..."
        python3 benchmarks/db_manager.py generate \
            --corpus benchmarks/corpus_5000mb \
            --engine "$ENGINE" \
            --db-size 5 \
            --output "$DB"
    fi
done

echo "✓ Databases ready"
echo

# Run quick benchmarks
echo "4. Running quick benchmarks (10s each)..."
echo

for ENGINE in tantivy fts5; do
    DB="benchmarks/db_5mb_${ENGINE}.db"

    echo "  Testing $ENGINE..."
    ./target/release/tantivy-bench "$DB" "$ENGINE" \
        --duration 10 \
        --threads 4 \
        --cache 0.10 \
        --query exact \
        | grep -E "(Engine|Documents|Throughput|Latency P99|Cache|File size)" \
        | sed 's/^/    /'
    echo
done

echo "=== Test Complete ==="
echo
echo "To run full benchmarks:"
echo "  python3 benchmarks/run_benchmarks.py"
echo
echo "To test different configurations:"
echo "  ./target/release/tantivy-bench benchmarks/db_5mb_tantivy.db tantivy --cache 0.25 --query phrase"
