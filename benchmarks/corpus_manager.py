#!/usr/bin/env python3
"""
Corpus management for sqlite-tantivy benchmarks.

Commands:
  list      - List available corpus files
  generate  - Generate a new corpus of specified size
  info      - Show information about a corpus file
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Tuple
from download_corpus import generate_corpus_streaming

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:
    print("Error: pyarrow is required. Install with: pip install pyarrow")
    sys.exit(1)


def list_corpuses(folder: Path):
    """List all corpus directories in the specified folder."""
    corpus_dirs = sorted([d for d in folder.iterdir() if d.is_dir() and d.name.startswith("corpus_")])

    if not corpus_dirs:
        print(f"No corpus directories found in {folder}")
        print(f"\nGenerate a corpus with:")
        print(f"  python corpus_manager.py generate --size 10 --folder {folder}")
        return

    print(f"Available corpuses in {folder}:")
    print()
    print(f"{'Corpus':<40} {'Files':<8} {'Total Size':<12} {'Documents':<12} {'Text Size'}")
    print("-" * 100)

    for corpus_dir in corpus_dirs:
        try:
            # Read metadata
            metadata_file = corpus_dir / "_metadata.json"
            if metadata_file.exists():
                with open(metadata_file) as f:
                    metadata = json.load(f)

                parquet_files = list(corpus_dir.glob("part-*.parquet"))
                total_size = sum(f.stat().st_size for f in parquet_files)

                print(f"{corpus_dir.name:<40} {len(parquet_files):>6} {total_size / (1024*1024):>10.2f} MB "
                      f"{metadata['num_documents']:>10,} {metadata['text_size_mb']:>10.2f} MB")
            else:
                print(f"{corpus_dir.name:<40} No metadata found")
        except Exception as e:
            print(f"{corpus_dir.name:<40} ERROR: {e}")

    print()


def generate_corpus_file(size_mb: float, output_dir: Path, cache_dir: Path, file_size_mb: float = 50):
    """Generate a corpus and save as multiple Parquet files (streaming, low memory)."""
    print(f"Generating {size_mb} MB corpus from Project Gutenberg...")
    print(f"Cache directory: {cache_dir}")
    print(f"Output directory: {output_dir}")
    print(f"File size target: ~{file_size_mb} MB per file")
    print()

    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)

    # Define schema
    schema = pa.schema([
        ('author', pa.string()),
        ('title', pa.string()),
        ('body', pa.string())
    ])

    print(f"Writing Parquet files in {file_size_mb}MB chunks...")
    num_docs = 0
    total_text_size = 0
    file_num = 0
    current_batch_docs = []
    current_batch_size = 0
    target_file_bytes = file_size_mb * 1024 * 1024

    # Stream batches and write to Parquet files
    for batch in generate_corpus_streaming(size_mb, cache_dir):
        for doc in batch:
            author, title, body = doc
            current_batch_docs.append(doc)
            current_batch_size += len(author) + len(title) + len(body)
            num_docs += 1
            total_text_size += len(author) + len(title) + len(body)

            # Write file when we hit target size
            if current_batch_size >= target_file_bytes:
                # Write Parquet file
                output_file = output_dir / f"part-{file_num:05d}.parquet"
                table = pa.Table.from_pydict({
                    'author': [d[0] for d in current_batch_docs],
                    'title': [d[1] for d in current_batch_docs],
                    'body': [d[2] for d in current_batch_docs]
                }, schema=schema)

                pq.write_table(table, output_file, compression='zstd')

                file_size = output_file.stat().st_size
                print(f"  File {file_num}: {len(current_batch_docs):,} docs, "
                      f"{current_batch_size / (1024*1024):.1f} MB text → "
                      f"{file_size / (1024*1024):.1f} MB compressed")

                file_num += 1
                current_batch_docs = []
                current_batch_size = 0

    # Write remaining documents
    if current_batch_docs:
        output_file = output_dir / f"part-{file_num:05d}.parquet"
        table = pa.Table.from_pydict({
            'author': [d[0] for d in current_batch_docs],
            'title': [d[1] for d in current_batch_docs],
            'body': [d[2] for d in current_batch_docs]
        }, schema=schema)

        pq.write_table(table, output_file, compression='zstd')

        file_size = output_file.stat().st_size
        print(f"  File {file_num}: {len(current_batch_docs):,} docs, "
              f"{current_batch_size / (1024*1024):.1f} MB text → "
              f"{file_size / (1024*1024):.1f} MB compressed")

    # Save metadata
    metadata = {
        'size_mb': size_mb,
        'num_documents': num_docs,
        'text_size_mb': total_text_size / (1024 * 1024),
        'num_files': file_num + 1,
        'file_size_mb': file_size_mb
    }

    with open(output_dir / "_metadata.json", 'w') as f:
        json.dump(metadata, f, indent=2)

    # Calculate total compressed size
    parquet_files = list(output_dir.glob("part-*.parquet"))
    total_compressed = sum(f.stat().st_size for f in parquet_files)

    print(f"\nCorpus saved:")
    print(f"  Directory: {output_dir}")
    print(f"  Files: {len(parquet_files)}")
    print(f"  Documents: {num_docs:,}")
    print(f"  Text size: {total_text_size / (1024*1024):.2f} MB")
    print(f"  Compressed size: {total_compressed / (1024*1024):.2f} MB")
    print(f"  Compression ratio: {total_text_size / total_compressed:.2f}x")


def show_corpus_info(corpus_dir: Path):
    """Show detailed information about a corpus directory."""
    if not corpus_dir.exists():
        print(f"Error: {corpus_dir} does not exist")
        sys.exit(1)

    if not corpus_dir.is_dir():
        print(f"Error: {corpus_dir} is not a directory")
        sys.exit(1)

    print(f"Corpus: {corpus_dir}")
    print()

    # Read metadata
    metadata_file = corpus_dir / "_metadata.json"
    if metadata_file.exists():
        with open(metadata_file) as f:
            metadata = json.load(f)

        print("Metadata:")
        print(f"  Documents: {metadata['num_documents']:,}")
        print(f"  Text size: {metadata['text_size_mb']:.2f} MB")
        print(f"  Files: {metadata['num_files']}")
        print(f"  Target file size: {metadata.get('file_size_mb', 'N/A')} MB")
        print()

    # List Parquet files
    parquet_files = sorted(corpus_dir.glob("part-*.parquet"))
    total_size = sum(f.stat().st_size for f in parquet_files)

    print(f"Parquet files: {len(parquet_files)}")
    print(f"Total compressed size: {total_size / (1024*1024):.2f} MB")
    if metadata_file.exists():
        print(f"Compression ratio: {metadata['text_size_mb'] / (total_size / (1024*1024)):.2f}x")
    print()

    # Read first file to show sample
    if parquet_files:
        print("Sample documents (from first file):")
        table = pq.read_table(parquet_files[0])
        df = table.to_pandas()

        for i, row in df.head(5).iterrows():
            print(f"\n  {i+1}. {row['author']} - {row['title']}")
            print(f"     Length: {len(row['body']):,} chars")
            print(f"     Preview: {row['body'][:100].replace(chr(10), ' ')}...")


def main():
    parser = argparse.ArgumentParser(description="Manage benchmark corpuses")
    subparsers = parser.add_subparsers(dest='command', help='Command to run')

    # List command
    list_parser = subparsers.add_parser('list', help='List available corpus files')
    list_parser.add_argument('--folder', type=Path, default=Path('.'),
                            help='Folder to search for corpus files (default: current directory)')

    # Generate command
    gen_parser = subparsers.add_parser('generate', help='Generate a new corpus')
    gen_parser.add_argument('--size', type=float, required=True,
                           help='Corpus size in MB')
    gen_parser.add_argument('--output', type=Path,
                           help='Output file path (default: corpus_{size}mb.pkl)')
    gen_parser.add_argument('--folder', type=Path, default=Path('.'),
                           help='Output folder (default: current directory)')
    gen_parser.add_argument('--cache-dir', type=Path,
                           help='Cache directory for downloaded texts (default: gutenberg_cache)')

    # Info command
    info_parser = subparsers.add_parser('info', help='Show corpus information')
    info_parser.add_argument('corpus', type=Path, help='Corpus directory to inspect')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == 'list':
        list_corpuses(args.folder)

    elif args.command == 'generate':
        # Determine output directory
        if args.output:
            output_dir = args.output
        else:
            output_dir = args.folder / f"corpus_{int(args.size)}mb"

        # Determine cache directory
        cache_dir = args.cache_dir or (Path(__file__).parent / "gutenberg_cache")

        generate_corpus_file(args.size, output_dir, cache_dir)

    elif args.command == 'info':
        show_corpus_info(args.corpus)


if __name__ == "__main__":
    main()
