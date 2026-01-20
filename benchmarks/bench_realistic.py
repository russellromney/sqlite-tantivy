#!/usr/bin/env python3
"""
Realistic benchmark for sqlite-tantivy using Project Gutenberg texts.

Compares Tantivy vs FTS5 with various page cache sizes on real literary texts.

Usage:
    python benchmarks/bench_realistic.py --corpus-size 10 --cache-sizes "1000,5000,10000"
"""

import argparse
import json
import os
import random
import sqlite3
import sys
import time
from pathlib import Path
from typing import List, Dict, Tuple

# Import corpus downloader
from download_corpus import generate_corpus


# Default cache sizes to test (in pages, 4KB each)
# 1000 pages = 4MB, 5000 = 20MB, 10000 = 40MB, 20000 = 80MB
DEFAULT_CACHE_SIZES = [1000, 5000, 10000, 20000]


def time_operation(func, *args, **kwargs):
    """Time a function and return (result, elapsed_ms)."""
    start = time.perf_counter()
    result = func(*args, **kwargs)
    elapsed = (time.perf_counter() - start) * 1000
    return result, elapsed


def get_common_query_terms(corpus: List[Tuple[str, str, str]]) -> List[str]:
    """Extract common terms from corpus for realistic queries."""
    word_freq = {}

    # Sample some documents to find common words
    sample_size = min(100, len(corpus))
    samples = random.sample(corpus, sample_size)

    for _, _, text in samples:
        words = text.lower().split()
        for word in words:
            # Skip very short words
            if len(word) < 4:
                continue
            # Remove punctuation
            word = ''.join(c for c in word if c.isalnum())
            if word:
                word_freq[word] = word_freq.get(word, 0) + 1

    # Get most common words
    common_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)
    return [word for word, _ in common_words[:50]]


def benchmark_tantivy(db_path: str, extension_path: str, corpus: List[Tuple[str, str, str]],
                     query_terms: List[str], num_queries: int, cache_size: int) -> Dict:
    """Benchmark Tantivy with real corpus data."""
    num_docs = len(corpus)
    results = {
        "extension": "tantivy",
        "docs": num_docs,
        "queries": num_queries,
        "cache_size_pages": cache_size,
    }

    conn = sqlite3.connect(db_path)

    # Set page cache size BEFORE creating tables
    conn.execute(f"PRAGMA cache_size = {cache_size}")

    # Get actual cache and page size
    actual_cache = conn.execute("PRAGMA cache_size").fetchone()[0]
    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    results["actual_cache_size"] = actual_cache
    results["page_size"] = page_size
    results["cache_size_mb"] = (actual_cache * page_size) / (1024 * 1024)

    # Load extension
    conn.enable_load_extension(True)
    conn.load_extension(extension_path)

    # Create table
    conn.execute("CREATE VIRTUAL TABLE articles USING tantivy(author TEXT, title TEXT, body TEXT)")

    # Benchmark INSERT
    start = time.perf_counter()
    for rowid, (author, title, text) in enumerate(corpus, 1):
        conn.execute("INSERT INTO articles(rowid, author, title, body) VALUES (?, ?, ?, ?)",
                    (rowid, author, title, text))
    conn.commit()
    insert_time = (time.perf_counter() - start) * 1000

    results["insert_total_ms"] = insert_time
    results["insert_per_doc_ms"] = insert_time / num_docs
    results["insert_docs_per_sec"] = num_docs / (insert_time / 1000)

    # Get database size
    db_size = os.path.getsize(db_path)
    results["db_size_bytes"] = db_size
    results["db_size_mb"] = db_size / (1024 * 1024)

    # Benchmark queries
    query_results = []

    # Simple term queries
    for _ in range(num_queries):
        term = random.choice(query_terms)
        _, elapsed = time_operation(
            lambda: list(conn.execute(f"SELECT rowid, title FROM articles WHERE articles MATCH ?", (term,)))
        )
        query_results.append(("term", elapsed))

    # Phrase queries
    for _ in range(num_queries // 2):
        word1 = random.choice(query_terms)
        word2 = random.choice(query_terms)
        phrase = f'"{word1} {word2}"'
        _, elapsed = time_operation(
            lambda: list(conn.execute(f"SELECT rowid, title FROM articles WHERE articles MATCH ?", (phrase,)))
        )
        query_results.append(("phrase", elapsed))

    # Field queries
    for _ in range(num_queries // 2):
        term = random.choice(query_terms)
        query = f"title:{term}"
        _, elapsed = time_operation(
            lambda: list(conn.execute(f"SELECT rowid, title FROM articles WHERE articles MATCH ?", (query,)))
        )
        query_results.append(("field", elapsed))

    # Aggregate results
    for query_type in ["term", "phrase", "field"]:
        times = [t for qt, t in query_results if qt == query_type]
        if times:
            results[f"query_{query_type}_avg_ms"] = sum(times) / len(times)
            results[f"query_{query_type}_min_ms"] = min(times)
            results[f"query_{query_type}_max_ms"] = max(times)
            results[f"query_{query_type}_p50_ms"] = sorted(times)[len(times) // 2]
            results[f"query_{query_type}_p95_ms"] = sorted(times)[int(len(times) * 0.95)] if len(times) > 1 else times[0]

    conn.close()
    return results


def benchmark_fts5(db_path: str, corpus: List[Tuple[str, str, str]],
                  query_terms: List[str], num_queries: int, cache_size: int) -> Dict:
    """Benchmark FTS5 with real corpus data."""
    num_docs = len(corpus)
    results = {
        "extension": "fts5",
        "docs": num_docs,
        "queries": num_queries,
        "cache_size_pages": cache_size,
    }

    conn = sqlite3.connect(db_path)

    # Set page cache size
    conn.execute(f"PRAGMA cache_size = {cache_size}")

    actual_cache = conn.execute("PRAGMA cache_size").fetchone()[0]
    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    results["actual_cache_size"] = actual_cache
    results["page_size"] = page_size
    results["cache_size_mb"] = (actual_cache * page_size) / (1024 * 1024)

    # Create FTS5 table
    conn.execute("CREATE VIRTUAL TABLE articles USING fts5(author, title, body)")

    # Benchmark INSERT
    start = time.perf_counter()
    for author, title, text in corpus:
        conn.execute("INSERT INTO articles(author, title, body) VALUES (?, ?, ?)",
                    (author, title, text))
    conn.commit()
    insert_time = (time.perf_counter() - start) * 1000

    results["insert_total_ms"] = insert_time
    results["insert_per_doc_ms"] = insert_time / num_docs
    results["insert_docs_per_sec"] = num_docs / (insert_time / 1000)

    # Get database size
    db_size = os.path.getsize(db_path)
    results["db_size_bytes"] = db_size
    results["db_size_mb"] = db_size / (1024 * 1024)

    # Benchmark queries
    query_results = []

    # Simple term queries
    for _ in range(num_queries):
        term = random.choice(query_terms)
        _, elapsed = time_operation(
            lambda: list(conn.execute(f"SELECT rowid, title FROM articles WHERE articles MATCH ?", (term,)))
        )
        query_results.append(("term", elapsed))

    # Phrase queries
    for _ in range(num_queries // 2):
        word1 = random.choice(query_terms)
        word2 = random.choice(query_terms)
        phrase = f'"{word1} {word2}"'
        _, elapsed = time_operation(
            lambda: list(conn.execute(f"SELECT rowid, title FROM articles WHERE articles MATCH ?", (phrase,)))
        )
        query_results.append(("phrase", elapsed))

    # Column queries
    for _ in range(num_queries // 2):
        term = random.choice(query_terms)
        query = f"title:{term}"
        _, elapsed = time_operation(
            lambda: list(conn.execute(f"SELECT rowid, title FROM articles WHERE articles MATCH ?", (query,)))
        )
        query_results.append(("field", elapsed))

    # Aggregate results
    for query_type in ["term", "phrase", "field"]:
        times = [t for qt, t in query_results if qt == query_type]
        if times:
            results[f"query_{query_type}_avg_ms"] = sum(times) / len(times)
            results[f"query_{query_type}_min_ms"] = min(times)
            results[f"query_{query_type}_max_ms"] = max(times)
            results[f"query_{query_type}_p50_ms"] = sorted(times)[len(times) // 2]
            results[f"query_{query_type}_p95_ms"] = sorted(times)[int(len(times) * 0.95)] if len(times) > 1 else times[0]

    conn.close()
    return results


def print_comparison(all_results: Dict[int, Dict]):
    """Print formatted comparison table."""
    print("\n" + "=" * 100)
    print("CACHE SIZE BENCHMARK: Tantivy vs FTS5 on Project Gutenberg Texts")
    print("=" * 100)

    if not all_results:
        return

    sample = next(iter(all_results.values()))
    print(f"Documents: {sample['tantivy']['docs']:,}")
    print(f"Queries per type: {sample['tantivy']['queries']}")
    print()

    cache_sizes = sorted(all_results.keys())

    # Print header
    header = f"{'Metric':<40}"
    for cs in cache_sizes:
        header += f" | {cs:>6}p ({all_results[cs]['tantivy']['cache_size_mb']:.0f}MB)"
    print(header)
    print("-" * len(header))

    metrics = [
        ("TANTIVY", None, None),
        ("  Insert time (ms)", "insert_total_ms", "tantivy"),
        ("  Insert docs/sec", "insert_docs_per_sec", "tantivy"),
        ("  Term query avg (ms)", "query_term_avg_ms", "tantivy"),
        ("  Phrase query avg (ms)", "query_phrase_avg_ms", "tantivy"),
        ("  DB size (MB)", "db_size_mb", "tantivy"),
        ("", None, None),
        ("FTS5", None, None),
        ("  Insert time (ms)", "insert_total_ms", "fts5"),
        ("  Insert docs/sec", "insert_docs_per_sec", "fts5"),
        ("  Term query avg (ms)", "query_term_avg_ms", "fts5"),
        ("  Phrase query avg (ms)", "query_phrase_avg_ms", "fts5"),
        ("  DB size (MB)", "db_size_mb", "fts5"),
    ]

    for label, key, engine in metrics:
        if key is None:
            print(label)
        else:
            row = f"{label:<40}"
            for cs in cache_sizes:
                val = all_results[cs][engine].get(key, 0)
                row += f" | {val:>14.2f}"
            print(row)

    print()


def main():
    parser = argparse.ArgumentParser(description="Realistic benchmark with Project Gutenberg texts")
    parser.add_argument("--extension", default="./target/release/libsqlite_tantivy",
                       help="Path to Tantivy extension")
    parser.add_argument("--corpus-size", type=float, default=10,
                       help="Corpus size in MB (default: 10)")
    parser.add_argument("--queries", type=int, default=100,
                       help="Number of queries per type (default: 100)")
    parser.add_argument("--cache-sizes", type=str,
                       help="Comma-separated cache sizes in pages")
    parser.add_argument("--output", help="Output JSON file")
    parser.add_argument("--no-fts5", action="store_true", help="Skip FTS5")
    args = parser.parse_args()

    # Parse cache sizes
    if args.cache_sizes:
        cache_sizes = [int(x.strip()) for x in args.cache_sizes.split(",")]
    else:
        cache_sizes = DEFAULT_CACHE_SIZES

    # Determine extension path
    ext_path = args.extension
    if sys.platform == "darwin" and not ext_path.endswith(".dylib"):
        ext_path += ".dylib"
    elif sys.platform == "linux" and not ext_path.endswith(".so"):
        ext_path += ".so"

    ext_path_no_ext = ext_path.rsplit(".", 1)[0]

    print(f"Realistic Benchmark: Tantivy vs FTS5")
    print(f"Extension: {ext_path}")
    print(f"Corpus size: {args.corpus_size} MB")
    print(f"Queries per type: {args.queries}")
    print(f"Cache sizes: {cache_sizes} pages")
    print()

    # Load corpus
    cache_dir = Path(__file__).parent / "gutenberg_cache"
    corpus = generate_corpus(args.corpus_size, cache_dir)

    print(f"\nCorpus loaded: {len(corpus)} documents")

    # Extract common query terms
    print("Extracting common query terms...")
    query_terms = get_common_query_terms(corpus)
    print(f"Using {len(query_terms)} common terms for queries")
    print(f"Sample terms: {', '.join(query_terms[:10])}")

    all_results = {}

    # Run benchmarks for each cache size
    import tempfile

    for cache_size in cache_sizes:
        print(f"\n{'='*80}")
        print(f"Testing cache size: {cache_size} pages (~{cache_size * 4 / 1024:.1f} MB)")
        print(f"{'='*80}")

        with tempfile.TemporaryDirectory() as tmpdir:
            tantivy_db = os.path.join(tmpdir, "tantivy.db")
            fts5_db = os.path.join(tmpdir, "fts5.db")

            print("\n[1/2] Benchmarking Tantivy...")
            tantivy_results = benchmark_tantivy(tantivy_db, ext_path_no_ext, corpus,
                                                query_terms, args.queries, cache_size)

            if not args.no_fts5:
                print("[2/2] Benchmarking FTS5...")
                fts5_results = benchmark_fts5(fts5_db, corpus, query_terms,
                                             args.queries, cache_size)
            else:
                fts5_results = {}

            all_results[cache_size] = {
                "tantivy": tantivy_results,
                "fts5": fts5_results,
            }

    # Print results
    print_comparison(all_results)

    # Save results
    if args.output:
        with open(args.output, "w") as f:
            json.dump(all_results, f, indent=2)
        print(f"Results saved to {args.output}")


if __name__ == "__main__":
    main()
