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
import pickle
import sqlite3
import sys
from pathlib import Path
from typing import List, Tuple, Dict


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


def generate_database(corpus_path: Path, output_path: Path, engine: str,
                     extension_path: str, compress: bool, encrypt: bool,
                     target_size_mb: float = None):
    """Generate a benchmark database from a corpus file."""
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

    if not corpus_path.exists():
        print(f"Error: Corpus file {corpus_path} does not exist")
        print(f"\nAvailable corpuses:")
        os.system(f"python3 {Path(__file__).parent / 'corpus_manager.py'} list")
        sys.exit(1)

    # Load corpus
    print(f"Loading corpus from {corpus_path}...")
    with open(corpus_path, 'rb') as f:
        corpus = pickle.load(f)

    print(f"  Loaded {len(corpus):,} documents from corpus")
    corpus_text_size = sum(len(a) + len(t) + len(txt) for a, t, txt in corpus)
    print(f"  Corpus text size: {corpus_text_size / (1024*1024):.2f} MB")

    # Adjust corpus to reach target size if specified
    if target_size_mb:
        target_bytes = target_size_mb * 1024 * 1024

        if target_bytes > corpus_text_size:
            # Need to duplicate documents
            print(f"  Target DB size: {target_size_mb:.0f} MB (larger than corpus)")
            print(f"  Duplicating documents to reach target size...")

            working_corpus = list(corpus)  # Start with original
            current_size = corpus_text_size
            cycle = 1

            while current_size < target_bytes:
                for doc in corpus:
                    if current_size >= target_bytes:
                        break
                    author, title, text = doc
                    # Add cycle marker to title for variety
                    working_corpus.append((author, f"{title} (Copy {cycle})", text))
                    current_size += len(author) + len(title) + len(text)
                cycle += 1

            corpus = working_corpus
            final_size = sum(len(a) + len(t) + len(txt) for a, t, txt in corpus)
            print(f"  Final corpus: {len(corpus):,} documents, {final_size / (1024*1024):.2f} MB")

        elif target_bytes < corpus_text_size:
            # Need to sample documents
            print(f"  Target DB size: {target_size_mb:.0f} MB (smaller than corpus)")
            print(f"  Sampling documents to reach target size...")

            import random
            sampled = []
            current_size = 0
            shuffled = list(corpus)
            random.shuffle(shuffled)

            for doc in shuffled:
                if current_size >= target_bytes:
                    break
                sampled.append(doc)
                author, title, text = doc
                current_size += len(author) + len(title) + len(text)

            corpus = sampled
            final_size = sum(len(a) + len(t) + len(txt) for a, t, txt in corpus)
            print(f"  Sampled corpus: {len(corpus):,} documents, {final_size / (1024*1024):.2f} MB")
        else:
            print(f"  Target size matches corpus size: {target_size_mb:.0f} MB")

    total_text = sum(len(a) + len(t) + len(txt) for a, t, txt in corpus)
    print()

    # Create database
    print(f"Creating {engine.upper()} database at {output_path}...")
    print(f"  Compression: {'Yes' if compress else 'No'}")
    print(f"  Encryption: {'Yes' if encrypt else 'No'}")
    print()

    conn = sqlite3.connect(str(output_path))

    if engine == 'tantivy':
        # Load tantivy extension
        if not extension_path:
            # Auto-detect extension path
            if sys.platform == "darwin":
                extension_path = "./target/release/libsqlite_tantivy.dylib"
            elif sys.platform == "linux":
                extension_path = "./target/release/libsqlite_tantivy.so"
            else:
                extension_path = "./target/release/libsqlite_tantivy.dll"

        # Remove extension for loading
        ext_no_ext = extension_path.rsplit('.', 1)[0] if '.' in extension_path else extension_path

        conn.enable_load_extension(True)
        try:
            conn.load_extension(ext_no_ext)
            print(f"  Loaded extension: {extension_path}")
        except Exception as e:
            print(f"Error loading extension: {e}")
            print(f"Make sure to build first: make release")
            sys.exit(1)

        # Create tantivy table
        conn.execute("CREATE VIRTUAL TABLE articles USING tantivy(author TEXT, title TEXT, body TEXT)")

        # Insert all documents using executemany
        print(f"  Inserting {len(corpus):,} documents and building index...")
        import time
        start_time = time.time()

        # Prepare all data
        data = [(i+1, author, title, text) for i, (author, title, text) in enumerate(corpus)]

        # Bulk insert
        conn.executemany(
            "INSERT INTO articles(rowid, author, title, body) VALUES (?, ?, ?, ?)",
            data
        )

        insert_time = time.time() - start_time
        print(f"  Done! Index built in {insert_time:.2f}s ({len(corpus)/insert_time:.0f} docs/sec)")

    elif engine == 'fts5':
        # Create FTS5 table
        conn.execute("CREATE VIRTUAL TABLE articles USING fts5(author, title, body)")

        # Insert all documents using executemany
        print(f"  Inserting {len(corpus):,} documents and building index...")
        import time
        start_time = time.time()

        # Bulk insert
        conn.executemany(
            "INSERT INTO articles(author, title, body) VALUES (?, ?, ?)",
            corpus
        )

        insert_time = time.time() - start_time
        print(f"  Done! Index built in {insert_time:.2f}s ({len(corpus)/insert_time:.0f} docs/sec)")

    else:
        print(f"Error: Unknown engine '{engine}'. Use 'tantivy' or 'fts5'")
        sys.exit(1)

    # Commit the single transaction
    conn.commit()

    # Save metadata (insert_time is defined in both branches above)
    metadata = {
        'corpus_file': str(corpus_path),
        'corpus_documents': len(corpus),
        'corpus_size_mb': total_text / (1024 * 1024),
        'target_size_mb': target_size_mb if target_size_mb else total_text / (1024 * 1024),
        'index_build_time_sec': insert_time,
        'index_build_docs_per_sec': len(corpus) / insert_time if insert_time > 0 else 0,
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
            print(f"  Corpus file: {metadata['corpus_file']}")
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
                           help='Corpus file to use')
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
                base_name = args.corpus.stem  # e.g., "corpus_10mb"

            flags = []
            if args.compress:
                flags.append('compressed')
            if args.encrypt:
                flags.append('encrypted')

            parts = [base_name, args.engine]
            parts.extend(flags)
            output_name = '_'.join(parts) + '.db'

            args.output = args.corpus.parent / output_name

        generate_database(args.corpus, args.output, args.engine,
                         args.extension, args.compress, args.encrypt,
                         args.db_size)

    elif args.command == 'info':
        show_database_info(args.database)


if __name__ == "__main__":
    main()
