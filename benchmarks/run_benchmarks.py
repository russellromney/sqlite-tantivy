#!/usr/bin/env python3
"""
Comprehensive benchmark runner for sqlite-tantivy vs FTS5.

Runs benchmarks across:
- Multiple database sizes (100MB, 500MB, 1GB, 5GB)
- Multiple cache sizes (5%, 10%, 15%, 20%, 25% of logical DB size)
- Multiple query types (exact, phrase, prefix, fuzzy)
- Both engines (tantivy, fts5)
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Dict
import time

# Default configurations
DEFAULT_DB_SIZES = [100, 500, 1000, 5000]  # MB
DEFAULT_CACHE_PERCENTS = [0.05, 0.10, 0.15, 0.20, 0.25]
DEFAULT_QUERY_TYPES = ["exact", "phrase", "prefix"]
DEFAULT_ENGINES = ["tantivy", "fts5"]
DEFAULT_DURATION = 30  # seconds
DEFAULT_THREADS = 4


def generate_database(corpus_dir: Path, engine: str, size_mb: int, output_dir: Path) -> Path:
    """Generate a benchmark database using db_manager.py."""
    db_path = output_dir / f"db_{size_mb}mb_{engine}.db"

    if db_path.exists():
        print(f"  Database already exists: {db_path.name}")
        return db_path

    print(f"  Generating {size_mb}MB {engine.upper()} database...")

    cmd = [
        "python3",
        "benchmarks/db_manager.py",
        "generate",
        "--corpus", str(corpus_dir),
        "--engine", engine,
        "--db-size", str(size_mb),
        "--output", str(db_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"  ERROR: Failed to generate database")
        print(result.stderr)
        sys.exit(1)

    print(f"  ✓ Generated: {db_path.name}")
    return db_path


def run_benchmark(
    db_path: Path,
    engine: str,
    cache_percent: float,
    query_type: str,
    duration_secs: int,
    threads: int,
) -> Dict:
    """Run a single benchmark configuration."""
    bench_bin = "./target/release/tantivy-bench"

    if not Path(bench_bin).exists():
        print(f"ERROR: Benchmark binary not found: {bench_bin}")
        print("Build it with: cargo build --release --bin tantivy-bench")
        sys.exit(1)

    cmd = [
        bench_bin,
        str(db_path),
        engine,
        "--duration", str(duration_secs),
        "--threads", str(threads),
        "--cache", str(cache_percent),
        "--query", query_type,
    ]

    print(f"    Running: cache={cache_percent:.0%}, query={query_type}...", end=" ", flush=True)

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print("ERROR")
        print(result.stderr)
        return None

    # Parse JSON output
    output_lines = result.stdout.strip().split('\n')
    json_start = None
    for i, line in enumerate(output_lines):
        if line.strip() == "JSON:":
            json_start = i + 1
            break

    if json_start is None:
        print("ERROR: Could not find JSON output")
        return None

    json_str = '\n'.join(output_lines[json_start:])
    result_data = json.loads(json_str)

    print(f"{result_data['read_ops_per_sec']:.0f} ops/sec, p99={result_data['read_p99_us']:.1f}µs")

    return result_data


def main():
    parser = argparse.ArgumentParser(description="Run comprehensive sqlite-tantivy benchmarks")
    parser.add_argument("--corpus", type=Path, default=Path("benchmarks/corpus_5000mb"),
                       help="Corpus directory (default: benchmarks/corpus_5000mb)")
    parser.add_argument("--db-sizes", type=int, nargs="+", default=DEFAULT_DB_SIZES,
                       help=f"Database sizes in MB (default: {DEFAULT_DB_SIZES})")
    parser.add_argument("--cache-percents", type=float, nargs="+", default=DEFAULT_CACHE_PERCENTS,
                       help=f"Cache sizes as % of logical DB (default: {DEFAULT_CACHE_PERCENTS})")
    parser.add_argument("--query-types", type=str, nargs="+", default=DEFAULT_QUERY_TYPES,
                       help=f"Query types (default: {DEFAULT_QUERY_TYPES})")
    parser.add_argument("--engines", type=str, nargs="+", default=DEFAULT_ENGINES,
                       help=f"Engines to test (default: {DEFAULT_ENGINES})")
    parser.add_argument("--duration", type=int, default=DEFAULT_DURATION,
                       help=f"Benchmark duration in seconds (default: {DEFAULT_DURATION})")
    parser.add_argument("--threads", type=int, default=DEFAULT_THREADS,
                       help=f"Number of reader threads (default: {DEFAULT_THREADS})")
    parser.add_argument("--output", type=Path, default=Path("benchmarks/results.json"),
                       help="Output JSON file (default: benchmarks/results.json)")
    parser.add_argument("--skip-generation", action="store_true",
                       help="Skip database generation, use existing databases")

    args = parser.parse_args()

    if not args.corpus.exists():
        print(f"ERROR: Corpus directory not found: {args.corpus}")
        print("Generate it with: python3 benchmarks/corpus_manager.py generate --size 5000")
        sys.exit(1)

    # Ensure benchmark binary is built
    bench_bin = Path("./target/release/tantivy-bench")
    if not bench_bin.exists():
        print("Building benchmark binary...")
        result = subprocess.run(["cargo", "build", "--release", "--bin", "tantivy-bench"],
                              capture_output=True, text=True)
        if result.returncode != 0:
            print("ERROR: Failed to build benchmark binary")
            print(result.stderr)
            sys.exit(1)
        print("✓ Built benchmark binary\n")

    output_dir = Path("benchmarks")
    output_dir.mkdir(exist_ok=True)

    all_results = []

    print("=== SQLite FTS Benchmark Suite ===")
    print(f"Database sizes: {args.db_sizes} MB")
    print(f"Cache sizes: {[f'{p:.0%}' for p in args.cache_percents]}")
    print(f"Query types: {args.query_types}")
    print(f"Engines: {args.engines}")
    print(f"Duration: {args.duration}s per test")
    print(f"Threads: {args.threads}")
    print()

    total_tests = len(args.db_sizes) * len(args.engines) * len(args.cache_percents) * len(args.query_types)
    test_count = 0

    for size_mb in args.db_sizes:
        print(f"\n{'='*60}")
        print(f"Database Size: {size_mb} MB")
        print(f"{'='*60}\n")

        for engine in args.engines:
            print(f"  Engine: {engine.upper()}")

            # Generate database
            if not args.skip_generation:
                db_path = generate_database(args.corpus, engine, size_mb, output_dir)
            else:
                db_path = output_dir / f"db_{size_mb}mb_{engine}.db"
                if not db_path.exists():
                    print(f"  ERROR: Database not found: {db_path}")
                    continue

            print()

            # Run benchmarks for each configuration
            for query_type in args.query_types:
                for cache_percent in args.cache_percents:
                    test_count += 1
                    print(f"  [{test_count}/{total_tests}] ", end="")

                    result = run_benchmark(
                        db_path, engine, cache_percent, query_type,
                        args.duration, args.threads
                    )

                    if result:
                        result['db_size_mb'] = size_mb
                        all_results.append(result)

            print()

    # Save results
    print(f"\n{'='*60}")
    print(f"Saving results to {args.output}")
    with open(args.output, 'w') as f:
        json.dump(all_results, f, indent=2)

    print(f"✓ Completed {len(all_results)} benchmarks")
    print()

    # Print summary
    print("=== Summary ===\n")

    # Group by engine and query type
    for engine in args.engines:
        print(f"{engine.upper()}:")
        for query_type in args.query_types:
            engine_results = [r for r in all_results
                            if r['engine'] == engine and r['query_type'] == query_type]
            if engine_results:
                avg_ops = sum(r['read_ops_per_sec'] for r in engine_results) / len(engine_results)
                avg_p99 = sum(r['read_p99_us'] for r in engine_results) / len(engine_results)
                print(f"  {query_type:8s}: {avg_ops:8.0f} ops/sec avg, {avg_p99:6.1f}µs p99 avg")
        print()


if __name__ == "__main__":
    main()
