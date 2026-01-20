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

    # Use DuckDB to create a temp table, then SQLite to copy into virtual table
    print(f"  Using DuckDB to process Parquet → temp table → {engine.upper()} index...")
    import time
    start_time = time.time()

    # Use DuckDB to create temporary database with regular table
    temp_db_path = output_path.parent / f"_temp_{output_path.name}"
    if temp_db_path.exists():
        temp_db_path.unlink()

    duckdb_conn = duckdb.connect()
    print(f"      Installing DuckDB SQLite extension...")
    duckdb_conn.execute("INSTALL sqlite")
    print(f"      Loading DuckDB SQLite extension...")
    duckdb_conn.execute("LOAD sqlite")
    print(f"      DuckDB ready!")

    # Build DuckDB query based on target size
    if target_bytes < corpus_bytes:
        sample_percent = (sample_ratio * 100)
        print(f"    Sampling {sample_percent:.1f}% of documents...")
        estimated_docs_needed = int(total_docs * sample_ratio)
        if estimated_docs_needed == 0:
            estimated_docs_needed = 1

        # Determine how many Parquet files we need to read
        # Each file is ~50MB text, ~781 docs per file on average
        import glob
        parquet_files = sorted(glob.glob(str(corpus_dir / "part-*.parquet")))
        docs_per_file = total_docs / len(parquet_files)
        files_needed = max(1, int(estimated_docs_needed / docs_per_file) + 1)

        # Select random subset of files if we don't need them all
        if files_needed < len(parquet_files):
            import random
            selected_files = random.sample(parquet_files, files_needed)
            print(f"      Reading {files_needed} of {len(parquet_files)} Parquet files...")
            # Create pattern for specific files
            file_list = ", ".join([f"'{f}'" for f in selected_files])
            query = f"""
                ATTACH '{temp_db_path}' AS db (TYPE SQLITE);
                CREATE TABLE db.articles AS
                SELECT author, title, body
                FROM read_parquet([{file_list}])
                LIMIT {estimated_docs_needed};
            """
        else:
            # Read all files
            parquet_pattern = str(corpus_dir / "part-*.parquet")
            query = f"""
                ATTACH '{temp_db_path}' AS db (TYPE SQLITE);
                CREATE TABLE db.articles AS
                SELECT author, title, body
                FROM read_parquet('{parquet_pattern}')
                LIMIT {estimated_docs_needed};
            """
    elif target_bytes > corpus_bytes:
        duplication_needed = int(target_bytes / corpus_bytes) + 1
        print(f"    Duplicating corpus {duplication_needed} times...")
        parquet_pattern = str(corpus_dir / "part-*.parquet")
        union_parts = []
        for i in range(duplication_needed):
            if i == 0:
                union_parts.append(f"SELECT author, title, body FROM read_parquet('{parquet_pattern}')")
            else:
                union_parts.append(f"SELECT author, title || ' (Copy {i})' as title, body FROM read_parquet('{parquet_pattern}')")

        union_query = " UNION ALL ".join(union_parts)
        query = f"""
            ATTACH '{temp_db_path}' AS db (TYPE SQLITE);
            CREATE TABLE db.articles AS {union_query};
        """
    else:
        print(f"    Inserting full corpus...")
        parquet_pattern = str(corpus_dir / "part-*.parquet")
        query = f"""
            ATTACH '{temp_db_path}' AS db (TYPE SQLITE);
            CREATE TABLE db.articles AS
            SELECT author, title, body
            FROM read_parquet('{parquet_pattern}');
        """

    # Execute DuckDB query to create temp database
    print(f"      Executing DuckDB query...")
    duckdb_conn.execute(query)
    print(f"      Query complete! Getting stats...")

    # Get stats from temp table
    result = duckdb_conn.execute("""
        SELECT
            COUNT(*) as doc_count,
            COALESCE(SUM(LENGTH(author) + LENGTH(title) + LENGTH(body)), 0) as total_bytes
        FROM db.articles
    """).fetchone()

    inserted_docs = result[0]
    total_text_bytes = result[1] or 0
    duckdb_conn.close()

    print(f"    DuckDB created temp table: {inserted_docs:,} documents, {total_text_bytes / (1024*1024):.1f} MB")

    # Now copy from temp database to virtual table using SQLite
    print(f"    Copying to {engine.upper()} virtual table...")
    conn = sqlite3.connect(str(output_path))

    # Load extension and create virtual table
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

    # Attach temp database and copy data
    conn.execute(f"ATTACH DATABASE '{temp_db_path}' AS source_db")

    if engine == 'tantivy':
        conn.execute("INSERT INTO articles(rowid, author, title, body) SELECT rowid, author, title, body FROM source_db.articles")
    else:
        conn.execute("INSERT INTO articles(author, title, body) SELECT author, title, body FROM source_db.articles")

    conn.commit()
    conn.execute("DETACH DATABASE source_db")

    # Clean up temp database
    temp_db_path.unlink()

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
