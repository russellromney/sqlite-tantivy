#!/usr/bin/env python3
"""
Benchmark sqlite-tantivy with various page cache sizes and compare to FTS5.

Usage:
    python benchmarks/bench_cache.py [--extension PATH] [--docs N] [--queries N]

Requires: sqlite3 CLI with extension loading enabled, matplotlib (optional for plots)
"""

import argparse
import json
import os
import random
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Dict, Tuple


# Sample words for generating test documents
WORDS = [
    "the", "quick", "brown", "fox", "jumps", "over", "lazy", "dog",
    "rust", "programming", "language", "performance", "memory", "safety",
    "sqlite", "database", "search", "index", "query", "full", "text",
    "tantivy", "lucene", "inverted", "document", "term", "token",
    "algorithm", "data", "structure", "tree", "hash", "table",
    "network", "server", "client", "request", "response", "api",
    "user", "system", "file", "process", "thread", "async", "await",
]

# Default page cache sizes to test (in pages, where 1 page = typically 4KB)
DEFAULT_CACHE_SIZES = [500, 1000, 2000, 4000, 8000]


def generate_document(min_words=20, max_words=100):
    """Generate a random document."""
    num_words = random.randint(min_words, max_words)
    return " ".join(random.choices(WORDS, k=num_words))


def generate_title(min_words=3, max_words=8):
    """Generate a random title."""
    num_words = random.randint(min_words, max_words)
    return " ".join(random.choices(WORDS, k=num_words)).title()


def time_operation(func, *args, **kwargs):
    """Time a function and return (result, elapsed_ms)."""
    start = time.perf_counter()
    result = func(*args, **kwargs)
    elapsed = (time.perf_counter() - start) * 1000
    return result, elapsed


def benchmark_tantivy(db_path, extension_path, num_docs, num_queries, cache_size):
    """Benchmark sqlite-tantivy extension with specified page cache size."""
    results = {
        "extension": "tantivy",
        "docs": num_docs,
        "queries": num_queries,
        "cache_size_pages": cache_size,
    }

    conn = sqlite3.connect(db_path)

    # Set page cache size
    conn.execute(f"PRAGMA cache_size = {cache_size}")

    # Verify cache size was set
    actual_cache = conn.execute("PRAGMA cache_size").fetchone()[0]
    results["actual_cache_size"] = actual_cache

    conn.enable_load_extension(True)
    conn.load_extension(extension_path)

    # Create table
    conn.execute("CREATE VIRTUAL TABLE articles USING tantivy(title TEXT, body TEXT)")

    # Benchmark INSERT
    docs = [(i, generate_title(), generate_document()) for i in range(1, num_docs + 1)]

    start = time.perf_counter()
    for rowid, title, body in docs:
        conn.execute("INSERT INTO articles(rowid, title, body) VALUES (?, ?, ?)", (rowid, title, body))
    conn.commit()
    insert_time = (time.perf_counter() - start) * 1000

    results["insert_total_ms"] = insert_time
    results["insert_per_doc_ms"] = insert_time / num_docs
    results["insert_docs_per_sec"] = num_docs / (insert_time / 1000)

    # Get database size
    db_size = os.path.getsize(db_path)
    results["db_size_bytes"] = db_size
    results["db_size_mb"] = db_size / (1024 * 1024)

    # Benchmark various query types
    query_results = []

    # Simple term search
    for _ in range(num_queries):
        term = random.choice(WORDS)
        _, elapsed = time_operation(
            lambda: list(conn.execute(f"SELECT rowid, title FROM articles WHERE articles MATCH '{term}'"))
        )
        query_results.append(("term", elapsed))

    # Phrase search
    for _ in range(num_queries):
        phrase = f'"{random.choice(WORDS)} {random.choice(WORDS)}"'
        _, elapsed = time_operation(
            lambda: list(conn.execute(f"SELECT rowid, title FROM articles WHERE articles MATCH {phrase}"))
        )
        query_results.append(("phrase", elapsed))

    # Field-scoped search
    for _ in range(num_queries):
        term = random.choice(WORDS)
        _, elapsed = time_operation(
            lambda: list(conn.execute(f"SELECT rowid, title FROM articles WHERE articles MATCH 'title:{term}'"))
        )
        query_results.append(("field", elapsed))

    # Aggregate query results by type
    for query_type in ["term", "phrase", "field"]:
        times = [t for qt, t in query_results if qt == query_type]
        results[f"query_{query_type}_avg_ms"] = sum(times) / len(times)
        results[f"query_{query_type}_min_ms"] = min(times)
        results[f"query_{query_type}_max_ms"] = max(times)
        results[f"query_{query_type}_p50_ms"] = sorted(times)[len(times) // 2]
        results[f"query_{query_type}_p95_ms"] = sorted(times)[int(len(times) * 0.95)]

    conn.close()
    return results


def benchmark_fts5(db_path, num_docs, num_queries, cache_size):
    """Benchmark SQLite FTS5 with specified page cache size."""
    results = {
        "extension": "fts5",
        "docs": num_docs,
        "queries": num_queries,
        "cache_size_pages": cache_size,
    }

    conn = sqlite3.connect(db_path)

    # Set page cache size
    conn.execute(f"PRAGMA cache_size = {cache_size}")

    # Verify cache size was set
    actual_cache = conn.execute("PRAGMA cache_size").fetchone()[0]
    results["actual_cache_size"] = actual_cache

    # Create FTS5 table
    conn.execute("CREATE VIRTUAL TABLE articles USING fts5(title, body)")

    # Benchmark INSERT
    docs = [(generate_title(), generate_document()) for _ in range(num_docs)]

    start = time.perf_counter()
    for title, body in docs:
        conn.execute("INSERT INTO articles(title, body) VALUES (?, ?)", (title, body))
    conn.commit()
    insert_time = (time.perf_counter() - start) * 1000

    results["insert_total_ms"] = insert_time
    results["insert_per_doc_ms"] = insert_time / num_docs
    results["insert_docs_per_sec"] = num_docs / (insert_time / 1000)

    # Get database size
    db_size = os.path.getsize(db_path)
    results["db_size_bytes"] = db_size
    results["db_size_mb"] = db_size / (1024 * 1024)

    # Benchmark various query types
    query_results = []

    # Simple term search
    for _ in range(num_queries):
        term = random.choice(WORDS)
        _, elapsed = time_operation(
            lambda: list(conn.execute(f"SELECT rowid, title FROM articles WHERE articles MATCH '{term}'"))
        )
        query_results.append(("term", elapsed))

    # Phrase search
    for _ in range(num_queries):
        phrase = f'"{random.choice(WORDS)} {random.choice(WORDS)}"'
        _, elapsed = time_operation(
            lambda: list(conn.execute(f"SELECT rowid, title FROM articles WHERE articles MATCH {phrase}"))
        )
        query_results.append(("phrase", elapsed))

    # Column filter search (FTS5 syntax)
    for _ in range(num_queries):
        term = random.choice(WORDS)
        _, elapsed = time_operation(
            lambda: list(conn.execute(f"SELECT rowid, title FROM articles WHERE articles MATCH 'title:{term}'"))
        )
        query_results.append(("field", elapsed))

    # Aggregate query results by type
    for query_type in ["term", "phrase", "field"]:
        times = [t for qt, t in query_results if qt == query_type]
        results[f"query_{query_type}_avg_ms"] = sum(times) / len(times)
        results[f"query_{query_type}_min_ms"] = min(times)
        results[f"query_{query_type}_max_ms"] = max(times)
        results[f"query_{query_type}_p50_ms"] = sorted(times)[len(times) // 2]
        results[f"query_{query_type}_p95_ms"] = sorted(times)[int(len(times) * 0.95)]

    conn.close()
    return results


def print_cache_comparison(all_results: Dict[int, Dict]):
    """Print comparison table across different cache sizes."""
    print("\n" + "=" * 90)
    print("CACHE SIZE BENCHMARK COMPARISON")
    print("=" * 90)

    if not all_results:
        print("No results to display")
        return

    # Get a sample result to extract metadata
    sample = next(iter(all_results.values()))
    tantivy_sample = sample.get("tantivy", {})

    print(f"Documents: {tantivy_sample.get('docs', 'N/A'):,}")
    print(f"Queries per type: {tantivy_sample.get('queries', 'N/A')}")
    print()

    # Print header
    cache_sizes = sorted(all_results.keys())
    header = f"{'Metric':<30}"
    for cache_size in cache_sizes:
        header += f" | Cache={cache_size:>5}p"
    print(header)
    print("-" * len(header))

    # Metrics to compare
    metrics = [
        ("TANTIVY:", None),
        ("  Insert (ms)", "insert_total_ms"),
        ("  Insert/doc (ms)", "insert_per_doc_ms"),
        ("  Term query avg (ms)", "query_term_avg_ms"),
        ("  Term query p95 (ms)", "query_term_p95_ms"),
        ("  Phrase query avg (ms)", "query_phrase_avg_ms"),
        ("  Field query avg (ms)", "query_field_avg_ms"),
        ("  DB size (MB)", "db_size_mb"),
        ("", None),
        ("FTS5:", None),
        ("  Insert (ms)", "insert_total_ms"),
        ("  Insert/doc (ms)", "insert_per_doc_ms"),
        ("  Term query avg (ms)", "query_term_avg_ms"),
        ("  Term query p95 (ms)", "query_term_p95_ms"),
        ("  Phrase query avg (ms)", "query_phrase_avg_ms"),
        ("  Field query avg (ms)", "query_field_avg_ms"),
        ("  DB size (MB)", "db_size_mb"),
    ]

    current_engine = "tantivy"
    for label, key in metrics:
        if label == "FTS5:":
            current_engine = "fts5"
            row = label
        elif label == "TANTIVY:":
            current_engine = "tantivy"
            row = label
        elif label == "":
            row = ""
        elif key:
            row = f"{label:<30}"
            for cache_size in cache_sizes:
                val = all_results[cache_size].get(current_engine, {}).get(key, 0)
                row += f" | {val:>12.3f}"
        else:
            row = label
        print(row)

    print()


def print_speedup_table(all_results: Dict[int, Dict]):
    """Print speedup table comparing Tantivy vs FTS5."""
    print("\n" + "=" * 90)
    print("TANTIVY SPEEDUP vs FTS5 (higher is better)")
    print("=" * 90)

    cache_sizes = sorted(all_results.keys())
    header = f"{'Metric':<35}"
    for cache_size in cache_sizes:
        header += f" | {cache_size:>5}p"
    print(header)
    print("-" * len(header))

    metrics = [
        ("Insert speedup (x)", "insert_total_ms", True),  # True = lower is better, so invert
        ("Term query speedup (x)", "query_term_avg_ms", True),
        ("Phrase query speedup (x)", "query_phrase_avg_ms", True),
        ("Field query speedup (x)", "query_field_avg_ms", True),
        ("Size efficiency (x)", "db_size_mb", True),
    ]

    for label, key, invert in metrics:
        row = f"{label:<35}"
        for cache_size in cache_sizes:
            tantivy_val = all_results[cache_size].get("tantivy", {}).get(key, 0)
            fts5_val = all_results[cache_size].get("fts5", {}).get(key, 0)

            if tantivy_val > 0 and fts5_val > 0:
                if invert:
                    speedup = fts5_val / tantivy_val
                else:
                    speedup = tantivy_val / fts5_val
                row += f" | {speedup:>6.2f}x"
            else:
                row += f" | {'N/A':>7}"
        print(row)

    print()


def save_results(all_results: Dict[int, Dict], output_path: str):
    """Save results to JSON file."""
    with open(output_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"Results saved to {output_path}")


def create_plots(all_results: Dict[int, Dict], output_dir: str):
    """Create visualization plots (requires matplotlib)."""
    try:
        import matplotlib.pyplot as plt
        import matplotlib
        matplotlib.use('Agg')  # Use non-interactive backend
    except ImportError:
        print("matplotlib not available, skipping plots")
        return

    os.makedirs(output_dir, exist_ok=True)
    cache_sizes = sorted(all_results.keys())

    # Extract data for plotting
    tantivy_insert = [all_results[c]["tantivy"]["insert_total_ms"] for c in cache_sizes]
    fts5_insert = [all_results[c]["fts5"]["insert_total_ms"] for c in cache_sizes]

    tantivy_term = [all_results[c]["tantivy"]["query_term_avg_ms"] for c in cache_sizes]
    fts5_term = [all_results[c]["fts5"]["query_term_avg_ms"] for c in cache_sizes]

    tantivy_phrase = [all_results[c]["tantivy"]["query_phrase_avg_ms"] for c in cache_sizes]
    fts5_phrase = [all_results[c]["fts5"]["query_phrase_avg_ms"] for c in cache_sizes]

    # Plot 1: Insert performance
    plt.figure(figsize=(10, 6))
    plt.plot(cache_sizes, tantivy_insert, marker='o', label='Tantivy', linewidth=2)
    plt.plot(cache_sizes, fts5_insert, marker='s', label='FTS5', linewidth=2)
    plt.xlabel('Cache Size (pages)', fontsize=12)
    plt.ylabel('Insert Time (ms)', fontsize=12)
    plt.title('Insert Performance vs Cache Size', fontsize=14)
    plt.legend(fontsize=11)
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, 'insert_performance.png'), dpi=150)
    plt.close()

    # Plot 2: Query performance
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

    # Term queries
    ax1.plot(cache_sizes, tantivy_term, marker='o', label='Tantivy', linewidth=2)
    ax1.plot(cache_sizes, fts5_term, marker='s', label='FTS5', linewidth=2)
    ax1.set_xlabel('Cache Size (pages)', fontsize=12)
    ax1.set_ylabel('Avg Query Time (ms)', fontsize=12)
    ax1.set_title('Term Query Performance', fontsize=13)
    ax1.legend(fontsize=10)
    ax1.grid(True, alpha=0.3)

    # Phrase queries
    ax2.plot(cache_sizes, tantivy_phrase, marker='o', label='Tantivy', linewidth=2)
    ax2.plot(cache_sizes, fts5_phrase, marker='s', label='FTS5', linewidth=2)
    ax2.set_xlabel('Cache Size (pages)', fontsize=12)
    ax2.set_ylabel('Avg Query Time (ms)', fontsize=12)
    ax2.set_title('Phrase Query Performance', fontsize=13)
    ax2.legend(fontsize=10)
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, 'query_performance.png'), dpi=150)
    plt.close()

    # Plot 3: Speedup comparison
    speedup_insert = [fts5_insert[i] / tantivy_insert[i] for i in range(len(cache_sizes))]
    speedup_term = [fts5_term[i] / tantivy_term[i] for i in range(len(cache_sizes))]
    speedup_phrase = [fts5_phrase[i] / tantivy_phrase[i] for i in range(len(cache_sizes))]

    plt.figure(figsize=(10, 6))
    plt.plot(cache_sizes, speedup_insert, marker='o', label='Insert', linewidth=2)
    plt.plot(cache_sizes, speedup_term, marker='s', label='Term Query', linewidth=2)
    plt.plot(cache_sizes, speedup_phrase, marker='^', label='Phrase Query', linewidth=2)
    plt.axhline(y=1.0, color='gray', linestyle='--', alpha=0.5, label='Break-even')
    plt.xlabel('Cache Size (pages)', fontsize=12)
    plt.ylabel('Speedup (Tantivy vs FTS5)', fontsize=12)
    plt.title('Tantivy Speedup vs FTS5 by Cache Size', fontsize=14)
    plt.legend(fontsize=11)
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(output_dir, 'speedup_comparison.png'), dpi=150)
    plt.close()

    print(f"Plots saved to {output_dir}/")


def main():
    parser = argparse.ArgumentParser(description="Benchmark sqlite-tantivy with various cache sizes")
    parser.add_argument("--extension", default="./target/release/libsqlite_tantivy",
                       help="Path to extension (without file extension)")
    parser.add_argument("--docs", type=int, default=10000, help="Number of documents")
    parser.add_argument("--queries", type=int, default=100, help="Queries per type")
    parser.add_argument("--cache-sizes", type=str,
                       help="Comma-separated cache sizes in pages (e.g., '500,1000,2000')")
    parser.add_argument("--output", help="Output JSON file")
    parser.add_argument("--plot-dir", help="Directory for output plots (requires matplotlib)")
    parser.add_argument("--no-fts5", action="store_true", help="Skip FTS5 comparison")
    args = parser.parse_args()

    # Parse cache sizes
    if args.cache_sizes:
        cache_sizes = [int(x.strip()) for x in args.cache_sizes.split(",")]
    else:
        cache_sizes = DEFAULT_CACHE_SIZES

    # Determine extension path
    ext_path = args.extension
    if sys.platform == "darwin":
        if not ext_path.endswith(".dylib"):
            ext_path = ext_path + ".dylib" if os.path.exists(ext_path + ".dylib") else ext_path
    elif sys.platform == "linux":
        if not ext_path.endswith(".so"):
            ext_path = ext_path + ".so" if os.path.exists(ext_path + ".so") else ext_path

    ext_path_no_ext = ext_path.rsplit(".", 1)[0] if "." in ext_path else ext_path

    print(f"Benchmarking with extension: {ext_path}")
    print(f"Documents: {args.docs:,}")
    print(f"Queries per type: {args.queries}")
    print(f"Cache sizes (pages): {cache_sizes}")
    print()

    all_results = {}

    # Run benchmarks for each cache size
    for cache_size in cache_sizes:
        print(f"\n{'='*70}")
        print(f"Testing with cache size: {cache_size} pages (~{cache_size * 4 / 1024:.1f} MB)")
        print(f"{'='*70}")

        with tempfile.TemporaryDirectory() as tmpdir:
            tantivy_db = os.path.join(tmpdir, f"tantivy_{cache_size}.db")
            fts5_db = os.path.join(tmpdir, f"fts5_{cache_size}.db")

            print("\nRunning tantivy benchmark...")
            tantivy_results = benchmark_tantivy(tantivy_db, ext_path_no_ext, args.docs, args.queries, cache_size)

            if not args.no_fts5:
                print("Running FTS5 benchmark...")
                fts5_results = benchmark_fts5(fts5_db, args.docs, args.queries, cache_size)
            else:
                fts5_results = {}

            all_results[cache_size] = {
                "tantivy": tantivy_results,
                "fts5": fts5_results,
            }

    # Print comparison tables
    print_cache_comparison(all_results)
    print_speedup_table(all_results)

    # Save results
    if args.output:
        save_results(all_results, args.output)

    # Create plots
    if args.plot_dir:
        create_plots(all_results, args.plot_dir)


if __name__ == "__main__":
    main()
