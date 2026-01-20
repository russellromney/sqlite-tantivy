#!/usr/bin/env python3
"""
Database management for sqlite-tantivy benchmarks.

Commands:
  list      - List available benchmark databases
  generate  - Generate a new benchmark database from a corpus
  info      - Show information about a database
"""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import List, Tuple, Dict

try:
    import duckdb
except ImportError:
    print("Error: duckdb is required. Install with: pip install duckdb")
    sys.exit(1)


def parse_db_name(db_path: Path) -> Dict[str, str]:
    """
    Parse configuration from database filename.

    Expected format: corpus{size}mb_tantivy|fts5_[compressed]_[encrypted].db
    Example: corpus10mb_tantivy_compressed.db
    """
    name = db_path.stem  # Remove .db extension
    parts = name.split('_')

    config = {
        'corpus_size': 'unknown',
        'engine': 'unknown',
        'compressed': False,
        'encrypted': False,
    }

    for part in parts:
        if part.startswith('corpus') and part.endswith('mb'):
            config['corpus_size'] = part.replace('corpus', '').replace('mb', '') + 'MB'
        elif part in ('tantivy', 'fts5'):
            config['engine'] = part
        elif part == 'compressed':
            config['compressed'] = True
        elif part == 'encrypted':
            config['encrypted'] = True

    return config


def list_databases(folder: Path):
    """List all benchmark database files in the specified folder."""
    db_files = sorted(folder.glob("corpus*.db"))

    if not db_files:
        print(f"No benchmark databases found in {folder}")
        print(f"\nGenerate a database with:")
        print(f"  python db_manager.py generate --corpus corpus_10mb.pkl --engine tantivy")
        return

    print(f"Available benchmark databases in {folder}:")
    print()
    print(f"{'Filename':<45} {'Size':<12} {'Engine':<10} {'Compressed':<12} {'Encrypted'}")
    print("-" * 100)

    for db_file in db_files:
        try:
            config = parse_db_name(db_file)
            file_size = db_file.stat().st_size

            compressed_str = "Yes" if config['compressed'] else "No"
            encrypted_str = "Yes" if config['encrypted'] else "No"

            print(f"{db_file.name:<45} {file_size / (1024*1024):>10.2f} MB "
                  f"{config['engine']:<10} {compressed_str:<12} {encrypted_str}")
        except Exception as e:
            print(f"{db_file.name:<45} ERROR: {e}")

    print()


def generate_database(corpus_dir: Path, output_path: Path, engine: str,
                     extension_path: str, compress: bool, encrypt: bool,
                     target_size_mb: float = None):
    """Generate a benchmark database from a corpus directory (streaming, low memory)."""
    # Check for compression/encryption support FIRST (fail early)
    if compress or encrypt:
        compress_vfs_path = Path(__file__).parent.parent / "sqlite-compress-vfs"
        if not compress_vfs_path.exists():
            print(f"Error: Compression/encryption requested but sqlite-compress-vfs not found")
            print(f"Expected location: {compress_vfs_path}")
            print(f"\nTo use compression/encryption:")
            print(f"  1. Clone sqlite-compress-vfs to {compress_vfs_path}")
            print(f"  2. Build the VFS extension")
            print(f"  3. Re-run this command")
            sys.exit(1)

    if not corpus_dir.exists() or not corpus_dir.is_dir():
        print(f"Error: Corpus directory {corpus_dir} does not exist")
        print(f"\nAvailable corpuses:")
        os.system(f"python3 {Path(__file__).parent / 'corpus_manager.py'} list")
        sys.exit(1)

    # Load corpus metadata
    metadata_file = corpus_dir / "_metadata.json"
    if not metadata_file.exists():
        print(f"Error: Metadata file not found in {corpus_dir}")
        sys.exit(1)

    with open(metadata_file) as f:
        corpus_metadata = json.load(f)

    corpus_text_size_mb = corpus_metadata['text_size_mb']
    total_docs = corpus_metadata['num_documents']

    print(f"Corpus: {corpus_dir}")
    print(f"  Documents: {total_docs:,}")
    print(f"  Text size: {corpus_text_size_mb:.2f} MB")
    print()

    # Determine sampling/duplication strategy
    if not target_size_mb:
        target_size_mb = corpus_text_size_mb

    target_bytes = target_size_mb * 1024 * 1024
    corpus_bytes = corpus_text_size_mb * 1024 * 1024

    if target_bytes < corpus_bytes:
        print(f"Target size: {target_size_mb:.0f} MB (sampling from corpus)")
        sample_ratio = target_bytes / corpus_bytes
    elif target_bytes > corpus_bytes:
        print(f"Target size: {target_size_mb:.0f} MB (duplicating corpus)")
        duplication_needed = int(target_bytes / corpus_bytes) + 1
    else:
        print(f"Target size: {target_size_mb:.0f} MB (using full corpus)")
        sample_ratio = 1.0

    print()

    # Create database
    print(f"Creating {engine.upper()} database at {output_path}...")
    print(f"  Compression: {'Yes' if compress else 'No'}")
    print(f"  Encryption: {'Yes' if encrypt else 'No'}")
    print()

    # Save extension info for later
    if engine == 'tantivy':
        # Auto-detect extension path
        if not extension_path:
            if sys.platform == "darwin":
                extension_path = "./target/release/libsqlite_tantivy.dylib"
            elif sys.platform == "linux":
                extension_path = "./target/release/libsqlite_tantivy.so"
            else:
                extension_path = "./target/release/libsqlite_tantivy.dll"

    # Use DuckDB for fast Parquet reading, but handle Tantivy inserts carefully
    print(f"  Using DuckDB to read Parquet and batch-insert to {engine.upper()}...")
    import time
    start_time = time.time()

    # Create database and virtual table first
    conn = sqlite3.connect(str(output_path))

    if engine == 'tantivy':
        ext_no_ext = extension_path.rsplit('.', 1)[0] if '.' in extension_path else extension_path
        conn.enable_load_extension(True)
        try:
            conn.load_extension(ext_no_ext)
            print(f"      Loaded extension: {extension_path}")
        except Exception as e:
            print(f"Error loading extension: {e}")
            print(f"Make sure to build first: make release")
            sys.exit(1)
        conn.execute("CREATE VIRTUAL TABLE articles USING tantivy(author TEXT, title TEXT, body TEXT)")
    elif engine == 'fts5':
        conn.execute("CREATE VIRTUAL TABLE articles USING fts5(author, title, body)")
    else:
        print(f"Error: Unknown engine '{engine}'. Use 'tantivy' or 'fts5'")
        sys.exit(1)

    # Use DuckDB to read and process Parquet
    duckdb_conn = duckdb.connect()

    # Build query to read from Parquet
    if target_bytes < corpus_bytes:
        sample_percent = (sample_ratio * 100)
        print(f"    Sampling {sample_percent:.1f}% of documents...")
        estimated_docs_needed = int(total_docs * sample_ratio)
        if estimated_docs_needed == 0:
            estimated_docs_needed = 1

        # Determine how many Parquet files to read
        import glob
        parquet_files = sorted(glob.glob(str(corpus_dir / "part-*.parquet")))
        docs_per_file = total_docs / len(parquet_files)
        files_needed = max(1, int(estimated_docs_needed / docs_per_file) + 1)

        if files_needed < len(parquet_files):
            import random
            selected_files = random.sample(parquet_files, files_needed)
            print(f"      Reading {files_needed} of {len(parquet_files)} Parquet files...")
            file_list = ", ".join([f"'{f}'" for f in selected_files])
            query = f"SELECT author, title, body FROM read_parquet([{file_list}]) LIMIT {estimated_docs_needed}"
        else:
            parquet_pattern = str(corpus_dir / "part-*.parquet")
            query = f"SELECT author, title, body FROM read_parquet('{parquet_pattern}') LIMIT {estimated_docs_needed}"
    else:
        parquet_pattern = str(corpus_dir / "part-*.parquet")
        query = f"SELECT author, title, body FROM read_parquet('{parquet_pattern}')"

    # Execute query and insert into SQLite
    print(f"      Reading data with DuckDB...")
    result = duckdb_conn.execute(query)

    print(f"      Inserting into {engine.upper()} (batch size: 100)...")
    inserted_docs = 0
    total_text_bytes = 0
    rowid = 1
    BATCH_SIZE = 100  # Small batches to avoid Tantivy commit issues

    # Disable autocommit for Tantivy - we'll commit manually in batches
    # Note: Tantivy extension still commits per-insert internally, but this helps with SQLite overhead
    conn.execute("BEGIN")

    batch_data = []
    while True:
        rows = result.fetchmany(BATCH_SIZE)
        if not rows:
            break

        for author, title, body in rows:
            if engine == 'tantivy':
                batch_data.append((rowid, author, title, body))
            else:
                batch_data.append((author, title, body))

            rowid += 1
            total_text_bytes += len(author or '') + len(title or '') + len(body or '')

        # Bulk insert batch
        if batch_data:
            if engine == 'tantivy':
                conn.executemany(
                    "INSERT INTO articles(rowid, author, title, body) VALUES (?, ?, ?, ?)",
                    batch_data
                )
            else:
                conn.executemany(
                    "INSERT INTO articles(author, title, body) VALUES (?, ?, ?)",
                    batch_data
                )

            inserted_docs += len(batch_data)
            conn.commit()  # Commit this batch
            conn.execute("BEGIN")  # Start new transaction

            print(f"        {inserted_docs:,} documents, {total_text_bytes / (1024*1024):.1f} MB...")
            batch_data = []

        # Stop if we've reached target size
        if target_bytes < corpus_bytes and total_text_bytes >= target_bytes:
            break

    conn.commit()  # Final commit
    duckdb_conn.close()

    # Enable WAL mode for better read concurrency
    print(f"    Enabling WAL mode...")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")

    # Checkpoint WAL to ensure data is in main database
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    insert_time = time.time() - start_time
    print(f"  Done! Index built in {insert_time:.2f}s ({inserted_docs/insert_time:.0f} docs/sec)")

    total_text = total_text_bytes

    # Save metadata
    metadata = {
        'corpus_dir': str(corpus_dir),
        'corpus_documents': inserted_docs,
        'corpus_size_mb': total_text / (1024 * 1024),
        'target_size_mb': target_size_mb if target_size_mb else total_text / (1024 * 1024),
        'index_build_time_sec': insert_time,
        'index_build_docs_per_sec': inserted_docs / insert_time if insert_time > 0 else 0,
        'engine': engine,
        'compressed': compress,
        'encrypted': encrypt,
    }

    conn.execute("CREATE TABLE IF NOT EXISTS _benchmark_metadata (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT OR REPLACE INTO _benchmark_metadata VALUES (?, ?)",
                ('metadata', json.dumps(metadata)))
    conn.commit()
    conn.close()

    # Show final stats
    db_size = output_path.stat().st_size
    print()
    print(f"Database created successfully:")
    print(f"  Path: {output_path}")
    print(f"  Size: {db_size / (1024*1024):.2f} MB")
    print(f"  Compression ratio: {total_text / db_size:.2f}x" if db_size > 0 else "")
    print()


def show_database_info(db_path: Path):
    """Show detailed information about a benchmark database."""
    if not db_path.exists():
        print(f"Error: {db_path} does not exist")
        sys.exit(1)

    config = parse_db_name(db_path)
    file_size = db_path.stat().st_size

    print(f"Database: {db_path}")
    print()
    print(f"Configuration (from filename):")
    print(f"  Engine: {config['engine']}")
    print(f"  Corpus size: {config['corpus_size']}")
    print(f"  Compressed: {'Yes' if config['compressed'] else 'No'}")
    print(f"  Encrypted: {'Yes' if config['encrypted'] else 'No'}")
    print(f"  File size: {file_size / (1024*1024):.2f} MB")
    print()

    # Try to read metadata
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.execute("SELECT value FROM _benchmark_metadata WHERE key = 'metadata'")
        row = cursor.fetchone()

        if row:
            metadata = json.loads(row[0])
            print("Metadata (from database):")
            print(f"  Corpus dir: {metadata['corpus_dir']}")
            print(f"  Documents: {metadata['corpus_documents']:,}")
            print(f"  Corpus text size: {metadata['corpus_size_mb']:.2f} MB")

            # Show target size if different from corpus size
            target_size = metadata.get('target_size_mb', metadata['corpus_size_mb'])
            if abs(target_size - metadata['corpus_size_mb']) > 0.1:  # More than 0.1 MB difference
                print(f"  Target DB size: {target_size:.2f} MB")

            # Show index build time if available
            if 'index_build_time_sec' in metadata:
                build_time = metadata['index_build_time_sec']
                docs_per_sec = metadata.get('index_build_docs_per_sec', 0)
                print(f"  Index build time: {build_time:.2f}s ({docs_per_sec:.0f} docs/sec)")

            print(f"  Compression ratio: {metadata['corpus_size_mb'] / (file_size / (1024*1024)):.2f}x")
            print()

        # Count documents
        try:
            cursor = conn.execute("SELECT COUNT(*) FROM articles")
            doc_count = cursor.fetchone()[0]
            print(f"Document count: {doc_count:,}")
        except:
            pass

        conn.close()

    except Exception as e:
        print(f"Could not read metadata: {e}")


def main():
    parser = argparse.ArgumentParser(description="Manage benchmark databases")
    subparsers = parser.add_subparsers(dest='command', help='Command to run')

    # List command
    list_parser = subparsers.add_parser('list', help='List available benchmark databases')
    list_parser.add_argument('--folder', type=Path, default=Path('.'),
                            help='Folder to search (default: current directory)')

    # Generate command
    gen_parser = subparsers.add_parser('generate', help='Generate a benchmark database')
    gen_parser.add_argument('--corpus', type=Path, required=True,
                           help='Corpus directory to use')
    gen_parser.add_argument('--engine', choices=['tantivy', 'fts5'], required=True,
                           help='Search engine to use')
    gen_parser.add_argument('--db-size', type=float,
                           help='Target database size in MB (can be larger or smaller than corpus)')
    gen_parser.add_argument('--output', type=Path,
                           help='Output database path (auto-generated if not specified)')
    gen_parser.add_argument('--extension', type=str,
                           help='Path to tantivy extension (auto-detected if not specified)')
    gen_parser.add_argument('--compress', action='store_true',
                           help='Enable compression (requires sqlite-compress-vfs)')
    gen_parser.add_argument('--encrypt', action='store_true',
                           help='Enable encryption (requires sqlite-compress-vfs)')

    # Info command
    info_parser = subparsers.add_parser('info', help='Show database information')
    info_parser.add_argument('database', type=Path, help='Database file to inspect')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == 'list':
        list_databases(args.folder)

    elif args.command == 'generate':
        # Auto-generate output path if not specified
        if not args.output:
            # Use db_size if specified, otherwise use corpus name
            if args.db_size:
                base_name = f"db_{int(args.db_size)}mb"
            else:
                base_name = args.corpus.name  # e.g., "corpus_5000mb"

            flags = []
            if args.compress:
                flags.append('compressed')
            if args.encrypt:
                flags.append('encrypted')

            parts = [base_name, args.engine]
            parts.extend(flags)
            output_name = '_'.join(parts) + '.db'

            args.output = Path('benchmarks') / output_name

        generate_database(args.corpus, args.output, args.engine,
                         args.extension, args.compress, args.encrypt,
                         args.db_size)

    elif args.command == 'info':
        show_database_info(args.database)


if __name__ == "__main__":
    main()
