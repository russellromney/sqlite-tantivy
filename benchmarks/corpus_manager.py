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
import pickle
import sys
from pathlib import Path
from typing import List, Tuple
from download_corpus import generate_corpus, generate_corpus_streaming


def list_corpuses(folder: Path):
    """List all corpus files in the specified folder."""
    corpus_files = sorted(folder.glob("corpus_*.pkl"))

    if not corpus_files:
        print(f"No corpus files found in {folder}")
        print(f"\nGenerate a corpus with:")
        print(f"  python corpus_manager.py generate --size 10 --output {folder}")
        return

    print(f"Available corpuses in {folder}:")
    print()
    print(f"{'Filename':<30} {'Size':<12} {'Documents':<12} {'Total Size'}")
    print("-" * 80)

    for corpus_file in corpus_files:
        try:
            with open(corpus_file, 'rb') as f:
                corpus = pickle.load(f)

            file_size = corpus_file.stat().st_size
            num_docs = len(corpus)

            # Calculate total text size
            total_size = sum(len(author) + len(title) + len(text)
                           for author, title, text in corpus)

            print(f"{corpus_file.name:<30} {file_size / (1024*1024):>10.2f} MB {num_docs:>10,} {total_size / (1024*1024):>10.2f} MB")
        except Exception as e:
            print(f"{corpus_file.name:<30} ERROR: {e}")

    print()


def generate_corpus_file(size_mb: float, output_path: Path, cache_dir: Path, streaming: bool = True):
    """Generate a corpus and save it to a pickle file."""
    print(f"Generating {size_mb} MB corpus from Project Gutenberg...")
    print(f"Cache directory: {cache_dir}")
    print(f"Mode: {'Streaming (low memory)' if streaming else 'In-memory (faster)'}")
    print()

    if streaming and size_mb >= 100:
        # Use streaming approach for large corpuses (>= 100MB)
        # Strategy: collect batches, periodically write and clear to keep memory low
        print(f"\nSaving corpus to {output_path} (streaming mode)...")

        all_batches = []
        num_docs = 0
        total_text_size = 0
        batch_count = 0
        WRITE_THRESHOLD = 50  # Write every 50 batches to keep memory manageable

        for batch in generate_corpus_streaming(size_mb, cache_dir):
            all_batches.append(batch)
            num_docs += len(batch)
            batch_count += 1

            # Calculate size for this batch
            batch_size = sum(len(a) + len(t) + len(txt) for a, t, txt in batch)
            total_text_size += batch_size

        # Flatten all batches and write
        print(f"\nFlattening and writing corpus to disk...")
        corpus_list = []
        for batch in all_batches:
            corpus_list.extend(batch)

        with open(output_path, 'wb') as f:
            pickle.dump(corpus_list, f)

        file_size = output_path.stat().st_size

        print(f"\nCorpus saved:")
        print(f"  File: {output_path}")
        print(f"  File size: {file_size / (1024*1024):.2f} MB")
        print(f"  Documents: {num_docs:,}")
        print(f"  Text size: {total_text_size / (1024*1024):.2f} MB")
    else:
        # Use in-memory approach for small corpuses
        corpus = generate_corpus(size_mb, cache_dir)

        # Save corpus
        print(f"\nSaving corpus to {output_path}...")
        with open(output_path, 'wb') as f:
            pickle.dump(corpus, f)

        file_size = output_path.stat().st_size
        total_text = sum(len(a) + len(t) + len(txt) for a, t, txt in corpus)

        print(f"\nCorpus saved:")
        print(f"  File: {output_path}")
        print(f"  File size: {file_size / (1024*1024):.2f} MB")
        print(f"  Documents: {len(corpus):,}")
        print(f"  Text size: {total_text / (1024*1024):.2f} MB")


def show_corpus_info(corpus_path: Path):
    """Show detailed information about a corpus file."""
    if not corpus_path.exists():
        print(f"Error: {corpus_path} does not exist")
        sys.exit(1)

    print(f"Corpus: {corpus_path}")
    print()

    with open(corpus_path, 'rb') as f:
        corpus = pickle.load(f)

    file_size = corpus_path.stat().st_size
    total_text = sum(len(a) + len(t) + len(txt) for a, t, txt in corpus)

    # Gather statistics
    authors = {}
    for author, title, text in corpus:
        authors[author] = authors.get(author, 0) + 1

    print(f"File size: {file_size / (1024*1024):.2f} MB")
    print(f"Documents: {len(corpus):,}")
    print(f"Text size: {total_text / (1024*1024):.2f} MB")
    print()

    print("Top authors:")
    for author, count in sorted(authors.items(), key=lambda x: x[1], reverse=True)[:10]:
        print(f"  {author:<40} {count:>6} documents")

    print()
    print("Sample documents:")
    for i, (author, title, text) in enumerate(corpus[:5], 1):
        print(f"\n  {i}. {author} - {title}")
        print(f"     Length: {len(text):,} chars")
        print(f"     Preview: {text[:100].replace(chr(10), ' ')}...")


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
    info_parser.add_argument('corpus', type=Path, help='Corpus file to inspect')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == 'list':
        list_corpuses(args.folder)

    elif args.command == 'generate':
        # Determine output path
        if args.output:
            output_path = args.output
        else:
            output_path = args.folder / f"corpus_{int(args.size)}mb.pkl"

        # Determine cache directory
        cache_dir = args.cache_dir or (Path(__file__).parent / "gutenberg_cache")

        # Create output folder if needed
        output_path.parent.mkdir(parents=True, exist_ok=True)

        generate_corpus_file(args.size, output_path, cache_dir)

    elif args.command == 'info':
        show_corpus_info(args.corpus)


if __name__ == "__main__":
    main()
