#!/usr/bin/env python3
"""
Run benchmarks on existing sqlite-tantivy or FTS5 databases.

Usage:
    python benchmark.py --db corpus_10mb_tantivy.db --cache-sizes "1000,5000,10000"
"""

import argparse
import json
import os
import random
import sqlite3
import sys
import time
from pathlib import Path
from typing import Dict, List


def time_operation(func):
    """Time a function and return (result, elapsed_ms)."""
    start = time.perf_counter()
    result = func()
    elapsed = (time.perf_counter() - start) * 1000
    return result, elapsed


def get_database_metadata(db_path: Path) -> Dict:
    """Read metadata from database."""
    conn = sqlite3.connect(str(db_path))

    try:
        cursor = conn.execute("SELECT value FROM _benchmark_metadata WHERE key = 'metadata'")
        row = cursor.fetchone()
        if row:
            metadata = json.loads(row[0])
        else:
            metadata = {}
    except:
        metadata = {}

    # Get document count
    try:
        cursor = conn.execute("SELECT COUNT(*) FROM articles")
        metadata['document_count'] = cursor.fetchone()[0]
    except:
        metadata['document_count'] = 0

    conn.close()
    return metadata


def extract_common_terms(db_path: Path, engine: str, sample_size: int = 100) -> List[str]:
    """Extract common terms from database for realistic queries."""
    conn = sqlite3.connect(str(db_path))

    # Sample documents
    try:
        cursor = conn.execute(f"SELECT body FROM articles LIMIT {sample_size}")
        texts = [row[0] for row in cursor.fetchall()]
    except Exception as e:
        print(f"Warning: Could not sample documents: {e}")
        texts = []

    conn.close()

    if not texts:
        # Fallback to common words
        return ['love', 'death', 'time', 'life', 'world', 'man', 'woman',
                'god', 'soul', 'heart', 'king', 'queen', 'lord', 'light', 'dark']

    # Count word frequencies
    word_freq = {}
    for text in texts:
        words = text.lower().split()
        for word in words:
            if len(word) < 4:
                continue
            word = ''.join(c for c in word if c.isalnum())
            if word:
                word_freq[word] = word_freq.get(word, 0) + 1

    # Return most common words
    common_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)
    return [word for word, _ in common_words[:50]]


def run_benchmark(db_path: Path, cache_size: int, num_queries: int, query_terms: List[str]) -> Dict:
    """Run benchmark on database with specified cache size."""
    results = {
        'cache_size_pages': cache_size,
        'queries': num_queries,
    }

    # Open database
    conn = sqlite3.connect(str(db_path))

    # Set cache size BEFORE any queries
    conn.execute(f"PRAGMA cache_size = {cache_size}")

    # Verify settings
    actual_cache = conn.execute("PRAGMA cache_size").fetchone()[0]
    page_size = conn.execute("PRAGMA page_size").fetchone()[0]

    results['actual_cache_size'] = actual_cache
    results['page_size'] = page_size
    results['cache_size_mb'] = (actual_cache * page_size) / (1024 * 1024)

    # Get database size
    db_size = db_path.stat().st_size
    results['db_size_bytes'] = db_size
    results['db_size_mb'] = db_size / (1024 * 1024)

    # Warm up - run a few queries to populate cache
    try:
        for _ in range(5):
            conn.execute("SELECT * FROM articles LIMIT 10").fetchall()
    except:
        pass

    # Run benchmark queries
    query_results = []

    print(f"  Running {num_queries} term queries...", end='', flush=True)
    for _ in range(num_queries):
        term = random.choice(query_terms)
        try:
            _, elapsed = time_operation(
                lambda: conn.execute("SELECT rowid, title FROM articles WHERE articles MATCH ?", (term,)).fetchall()
            )
            query_results.append(('term', elapsed))
        except Exception as e:
            # Some queries might fail, skip them
            pass
    print(" done")

    print(f"  Running {num_queries // 2} phrase queries...", end='', flush=True)
    for _ in range(num_queries // 2):
        word1 = random.choice(query_terms)
        word2 = random.choice(query_terms)
        phrase = f'"{word1} {word2}"'
        try:
            _, elapsed = time_operation(
                lambda: conn.execute("SELECT rowid, title FROM articles WHERE articles MATCH ?", (phrase,)).fetchall()
            )
            query_results.append(('phrase', elapsed))
        except Exception as e:
            pass
    print(" done")

    print(f"  Running {num_queries // 2} field queries...", end='', flush=True)
    for _ in range(num_queries // 2):
        term = random.choice(query_terms)
        query = f"title:{term}"
        try:
            _, elapsed = time_operation(
                lambda: conn.execute("SELECT rowid, title FROM articles WHERE articles MATCH ?", (query,)).fetchall()
            )
            query_results.append(('field', elapsed))
        except Exception as e:
            pass
    print(" done")

    # Aggregate results by query type
    for query_type in ['term', 'phrase', 'field']:
        times = [t for qt, t in query_results if qt == query_type]
        if times:
            results[f'query_{query_type}_count'] = len(times)
            results[f'query_{query_type}_avg_ms'] = sum(times) / len(times)
            results[f'query_{query_type}_min_ms'] = min(times)
            results[f'query_{query_type}_max_ms'] = max(times)
            results[f'query_{query_type}_p50_ms'] = sorted(times)[len(times) // 2]
            results[f'query_{query_type}_p95_ms'] = sorted(times)[int(len(times) * 0.95)] if len(times) > 1 else times[0]

    conn.close()
    return results


def print_results(all_results: Dict[int, Dict], metadata: Dict):
    """Print formatted benchmark results."""
    print("\n" + "=" * 100)
    print("BENCHMARK RESULTS")
    print("=" * 100)

    print(f"Database: {metadata.get('db_path', 'Unknown')}")
    print(f"Engine: {metadata.get('engine', 'Unknown')}")
    print(f"Documents: {metadata.get('document_count', 'Unknown'):,}")
    print(f"Corpus size: {metadata.get('corpus_size_mb', 0):.2f} MB")
    print(f"Database size: {metadata.get('db_size_mb', 0):.2f} MB")

    if metadata.get('compressed'):
        print(f"Compression: Enabled")
    if metadata.get('encrypted'):
        print(f"Encryption: Enabled")

    print()

    cache_sizes = sorted(all_results.keys())

    # Print header
    header = f"{'Metric':<35}"
    for cs in cache_sizes:
        cache_mb = all_results[cs]['cache_size_mb']
        header += f" | {cs:>6}p ({cache_mb:>4.0f}MB)"
    print(header)
    print("-" * len(header))

    # Metrics to display
    metrics = [
        ("Query Performance", None),
        ("  Term query avg (ms)", "query_term_avg_ms"),
        ("  Term query p50 (ms)", "query_term_p50_ms"),
        ("  Term query p95 (ms)", "query_term_p95_ms"),
        ("  Phrase query avg (ms)", "query_phrase_avg_ms"),
        ("  Phrase query p95 (ms)", "query_phrase_p95_ms"),
        ("  Field query avg (ms)", "query_field_avg_ms"),
        ("", None),
        ("Database Info", None),
        ("  DB size (MB)", "db_size_mb"),
        ("  Cache/DB ratio", None),  # Special calculation
    ]

    for label, key in metrics:
        if key is None:
            if label:  # Section header
                print(label)
            else:  # Blank line
                print()
        elif label == "  Cache/DB ratio":
            row = f"{label:<35}"
            for cs in cache_sizes:
                db_size_mb = all_results[cs]['db_size_mb']
                cache_mb = all_results[cs]['cache_size_mb']
                ratio = cache_mb / db_size_mb if db_size_mb > 0 else 0
                row += f" | {ratio:>14.2f}x"
            print(row)
        else:
            row = f"{label:<35}"
            for cs in cache_sizes:
                val = all_results[cs].get(key, 0)
                row += f" | {val:>15.2f}"
            print(row)

    print()

    # Print insights
    print("Insights:")

    # Find best cache size
    best_cache = min(cache_sizes, key=lambda cs: all_results[cs].get('query_term_avg_ms', float('inf')))
    best_time = all_results[best_cache].get('query_term_avg_ms', 0)
    worst_cache = max(cache_sizes, key=lambda cs: all_results[cs].get('query_term_avg_ms', 0))
    worst_time = all_results[worst_cache].get('query_term_avg_ms', float('inf'))

    speedup = worst_time / best_time if best_time > 0 else 1
    print(f"  Best cache size: {best_cache} pages ({all_results[best_cache]['cache_size_mb']:.0f}MB)")
    print(f"  Speedup vs worst: {speedup:.2f}x faster")

    # Check if cache covers full DB
    db_size_mb = all_results[cache_sizes[0]]['db_size_mb']
    for cs in cache_sizes:
        cache_mb = all_results[cs]['cache_size_mb']
        if cache_mb >= db_size_mb:
            print(f"  Cache size {cs} pages ({cache_mb:.0f}MB) fully covers database ({db_size_mb:.0f}MB)")
            break

    print()


def main():
    parser = argparse.ArgumentParser(description="Run benchmarks on existing database")
    parser.add_argument('--db', type=Path, required=True,
                       help='Database file to benchmark')
    parser.add_argument('--cache-sizes', type=str, default='1000,5000,10000',
                       help='Comma-separated cache sizes in pages (default: 1000,5000,10000)')
    parser.add_argument('--queries', type=int, default=100,
                       help='Number of queries per type (default: 100)')
    parser.add_argument('--output', type=Path,
                       help='Output JSON file for results')

    args = parser.parse_args()

    # Check database exists
    if not args.db.exists():
        print(f"Error: Database {args.db} does not exist")
        print()
        print("Available databases:")
        os.system(f"python3 {Path(__file__).parent / 'db_manager.py'} list")
        sys.exit(1)

    # Parse cache sizes
    cache_sizes = [int(x.strip()) for x in args.cache_sizes.split(',')]

    print(f"Benchmark Configuration")
    print(f"  Database: {args.db}")
    print(f"  Cache sizes: {cache_sizes}")
    print(f"  Queries per type: {args.queries}")
    print()

    # Get metadata
    metadata = get_database_metadata(args.db)
    metadata['db_path'] = str(args.db)
    metadata['db_size_mb'] = args.db.stat().st_size / (1024 * 1024)

    print(f"Database Info:")
    print(f"  Engine: {metadata.get('engine', 'Unknown')}")
    print(f"  Documents: {metadata.get('document_count', 'Unknown'):,}")
    print(f"  Size: {metadata['db_size_mb']:.2f} MB")
    print()

    # Extract query terms
    print("Extracting common query terms...")
    query_terms = extract_common_terms(args.db, metadata.get('engine', 'tantivy'))
    print(f"  Using {len(query_terms)} terms")
    print(f"  Sample: {', '.join(query_terms[:10])}")
    print()

    # Run benchmarks
    all_results = {}
    import tempfile
    import shutil

    for cache_size in cache_sizes:
        print(f"{'='*80}")
        print(f"Testing cache size: {cache_size} pages (~{cache_size * 4 / 1024:.1f} MB)")
        print(f"{'='*80}")

        # Create a temporary copy of the database for this test
        with tempfile.TemporaryDirectory() as tmpdir:
            db_copy = Path(tmpdir) / args.db.name
            print(f"  Creating test copy: {args.db.name}")
            shutil.copy2(args.db, db_copy)

            # Run benchmark on the copy
            results = run_benchmark(db_copy, cache_size, args.queries, query_terms)
            all_results[cache_size] = results

            print(f"  Completed - Avg term query: {results.get('query_term_avg_ms', 0):.2f}ms")
            print()

    # Print results
    print_results(all_results, metadata)

    # Save results
    if args.output:
        output_data = {
            'metadata': metadata,
            'results': all_results,
        }
        with open(args.output, 'w') as f:
            json.dump(output_data, f, indent=2)
        print(f"Results saved to {args.output}")


if __name__ == "__main__":
    main()
