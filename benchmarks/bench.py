#!/usr/bin/env python3
"""
Benchmark script for sqlite-tantivy extension.

Usage:
    python benchmarks/bench.py [--extension PATH] [--docs N] [--queries N]

Requires: sqlite3 CLI with extension loading enabled
"""

import argparse
import json
import os
import random
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path


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


def benchmark_tantivy(db_path, extension_path, num_docs, num_queries):
    """Benchmark sqlite-tantivy extension."""
    results = {"extension": "tantivy", "docs": num_docs, "queries": num_queries}

    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    conn.load_extension(extension_path)

    # Create table
    conn.execute("CREATE VIRTUAL TABLE articles USING tantivy(title TEXT, body TEXT)")

    # Benchmark INSERT
    docs = [(i, generate_title(), generate_document()) for i in range(1, num_docs + 1)]

    start = time.perf_counter()
    for rowid, title, body in docs:
        conn.execute("INSERT INTO articles(rowid, title, body) VALUES (?, ?, ?)", (rowid, title, body))
    insert_time = (time.perf_counter() - start) * 1000

    results["insert_total_ms"] = insert_time
    results["insert_per_doc_ms"] = insert_time / num_docs
    results["insert_docs_per_sec"] = num_docs / (insert_time / 1000)

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

    conn.close()
    return results


def benchmark_fts5(db_path, num_docs, num_queries):
    """Benchmark SQLite FTS5 for comparison."""
    results = {"extension": "fts5", "docs": num_docs, "queries": num_queries}

    conn = sqlite3.connect(db_path)

    # Create FTS5 table
    conn.execute("CREATE VIRTUAL TABLE articles USING fts5(title, body)")

    # Benchmark INSERT
    docs = [(generate_title(), generate_document()) for _ in range(num_docs)]

    start = time.perf_counter()
    for title, body in docs:
        conn.execute("INSERT INTO articles(title, body) VALUES (?, ?)", (title, body))
    insert_time = (time.perf_counter() - start) * 1000

    results["insert_total_ms"] = insert_time
    results["insert_per_doc_ms"] = insert_time / num_docs
    results["insert_docs_per_sec"] = num_docs / (insert_time / 1000)

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

    conn.close()
    return results


def print_results(tantivy_results, fts5_results):
    """Print comparison table."""
    print("\n" + "=" * 70)
    print("BENCHMARK RESULTS")
    print("=" * 70)
    print(f"Documents: {tantivy_results['docs']:,}")
    print(f"Queries per type: {tantivy_results['queries']}")
    print()

    print(f"{'Metric':<35} {'Tantivy':>15} {'FTS5':>15}")
    print("-" * 70)

    metrics = [
        ("Insert total (ms)", "insert_total_ms"),
        ("Insert per doc (ms)", "insert_per_doc_ms"),
        ("Insert docs/sec", "insert_docs_per_sec"),
        ("Term query avg (ms)", "query_term_avg_ms"),
        ("Phrase query avg (ms)", "query_phrase_avg_ms"),
        ("Field query avg (ms)", "query_field_avg_ms"),
    ]

    for label, key in metrics:
        t_val = tantivy_results.get(key, 0)
        f_val = fts5_results.get(key, 0)
        print(f"{label:<35} {t_val:>15.3f} {f_val:>15.3f}")

    print()


def main():
    parser = argparse.ArgumentParser(description="Benchmark sqlite-tantivy")
    parser.add_argument("--extension", default="./target/release/libsqlite_tantivy",
                       help="Path to extension (without file extension)")
    parser.add_argument("--docs", type=int, default=10000, help="Number of documents")
    parser.add_argument("--queries", type=int, default=100, help="Queries per type")
    parser.add_argument("--output", help="Output JSON file")
    parser.add_argument("--no-fts5", action="store_true", help="Skip FTS5 comparison")
    args = parser.parse_args()

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

    # Run benchmarks
    with tempfile.TemporaryDirectory() as tmpdir:
        tantivy_db = os.path.join(tmpdir, "tantivy.db")
        fts5_db = os.path.join(tmpdir, "fts5.db")

        print("\nRunning tantivy benchmark...")
        tantivy_results = benchmark_tantivy(tantivy_db, ext_path_no_ext, args.docs, args.queries)

        if not args.no_fts5:
            print("Running FTS5 benchmark...")
            fts5_results = benchmark_fts5(fts5_db, args.docs, args.queries)
        else:
            fts5_results = {}

        print_results(tantivy_results, fts5_results)

        if args.output:
            with open(args.output, "w") as f:
                json.dump({
                    "tantivy": tantivy_results,
                    "fts5": fts5_results,
                }, f, indent=2)
            print(f"Results saved to {args.output}")


if __name__ == "__main__":
    main()
