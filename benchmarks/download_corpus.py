#!/usr/bin/env python3
"""
Download public domain texts from Project Gutenberg to create benchmark corpus.

Downloads well-known works and caches them locally for repeated benchmark runs.
"""

import os
import re
import urllib.request
from pathlib import Path
from typing import List, Tuple


# Project Gutenberg texts to download (eBook ID, Author, Title)
GUTENBERG_TEXTS = [
    (100, "William Shakespeare", "The Complete Works of William Shakespeare"),
    (2701, "Herman Melville", "Moby Dick"),
    (1342, "Jane Austen", "Pride and Prejudice"),
    (84, "Mary Shelley", "Frankenstein"),
    (1661, "Arthur Conan Doyle", "The Adventures of Sherlock Holmes"),
    (11, "Lewis Carroll", "Alice's Adventures in Wonderland"),
    (1260, "Jane Austen", "Jane Austen's Complete Works"),
    (1400, "Homer (Samuel Butler)", "The Iliad"),
    (1727, "Homer (Samuel Butler)", "The Odyssey"),
    (2600, "Leo Tolstoy", "War and Peace"),
    (1497, "The Bible", "The King James Bible"),
    (16328, "James Joyce", "Ulysses"),
    (4300, "Charles Darwin", "On the Origin of Species"),
    (98, "Charles Dickens", "A Tale of Two Cities"),
    (1080, "Charles Dickens", "David Copperfield"),
    (1404, "Fyodor Dostoevsky", "The Brothers Karamazov"),
    (2554, "Fyodor Dostoevsky", "Crime and Punishment"),
    (205, "Plato", "The Republic"),
    (6130, "The Iliad", "The Iliad of Homer"),
    (29, "John Bunyan", "The Pilgrim's Progress"),
]


def clean_gutenberg_text(text: str) -> str:
    """Remove Project Gutenberg headers and footers."""
    # Find start of actual text
    start_markers = [
        "*** START OF THIS PROJECT GUTENBERG",
        "***START OF THE PROJECT GUTENBERG",
        "*** START OF THE PROJECT GUTENBERG",
    ]

    end_markers = [
        "*** END OF THIS PROJECT GUTENBERG",
        "***END OF THE PROJECT GUTENBERG",
        "*** END OF THE PROJECT GUTENBERG",
    ]

    for marker in start_markers:
        if marker in text:
            text = text.split(marker, 1)[1]
            # Skip the rest of that line
            text = text.split('\n', 1)[1] if '\n' in text else text
            break

    for marker in end_markers:
        if marker in text:
            text = text.split(marker, 1)[0]
            break

    return text.strip()


def download_gutenberg_text(ebook_id: int, author: str, title: str, cache_dir: Path) -> Tuple[str, str, str]:
    """
    Download a text from Project Gutenberg.

    Returns:
        Tuple of (author, title, text_content)
    """
    cache_file = cache_dir / f"{ebook_id}.txt"

    # Check cache first
    if cache_file.exists():
        print(f"  Using cached: {title}")
        with open(cache_file, 'r', encoding='utf-8') as f:
            text = f.read()
    else:
        # Download from Project Gutenberg
        # Try UTF-8 version first, fall back to ASCII
        urls = [
            f"https://www.gutenberg.org/files/{ebook_id}/{ebook_id}-0.txt",  # UTF-8
            f"https://www.gutenberg.org/files/{ebook_id}/{ebook_id}.txt",    # ASCII
        ]

        text = None
        for url in urls:
            try:
                print(f"  Downloading: {title} from {url}")
                with urllib.request.urlopen(url, timeout=30) as response:
                    text = response.read().decode('utf-8', errors='ignore')
                    break
            except Exception as e:
                print(f"    Failed: {e}")
                continue

        if text is None:
            print(f"  ERROR: Could not download {title}")
            return (author, title, "")

        # Clean and cache
        text = clean_gutenberg_text(text)

        with open(cache_file, 'w', encoding='utf-8') as f:
            f.write(text)

    return (author, title, text)


def generate_corpus(target_size_mb: float, cache_dir: Path) -> List[Tuple[str, str, str]]:
    """
    Generate a corpus of specified size from Project Gutenberg texts.

    Args:
        target_size_mb: Target size in megabytes
        cache_dir: Directory to cache downloaded texts

    Returns:
        List of (author, title, text) tuples
    """
    cache_dir.mkdir(parents=True, exist_ok=True)

    corpus = []
    current_size = 0
    target_bytes = int(target_size_mb * 1024 * 1024)

    # Download texts until we reach target size
    for ebook_id, author, title in GUTENBERG_TEXTS:
        if current_size >= target_bytes:
            break

        author_clean, title_clean, text = download_gutenberg_text(ebook_id, author, title, cache_dir)

        if text:
            # Split large texts into chapters/sections for more realistic documents
            sections = split_into_sections(text, author_clean, title_clean)
            corpus.extend(sections)

            text_size = sum(len(a) + len(t) + len(txt) for a, t, txt in sections)
            current_size += text_size

            print(f"  Added {len(sections)} sections, {text_size / (1024*1024):.2f} MB")
            print(f"  Total size: {current_size / (1024*1024):.2f} MB / {target_size_mb} MB")

    # If we need more, repeat texts with chapter markers
    cycle = 1
    while current_size < target_bytes:
        for ebook_id, author, title in GUTENBERG_TEXTS:
            if current_size >= target_bytes:
                break

            cache_file = cache_dir / f"{ebook_id}.txt"
            if not cache_file.exists():
                continue

            with open(cache_file, 'r', encoding='utf-8') as f:
                text = f.read()

            sections = split_into_sections(text, f"{author} (Cycle {cycle})", title)
            corpus.extend(sections)

            text_size = sum(len(a) + len(t) + len(txt) for a, t, txt in sections)
            current_size += text_size

        cycle += 1

    print(f"\nFinal corpus: {len(corpus)} documents, {current_size / (1024*1024):.2f} MB")
    return corpus


def generate_corpus_streaming(target_size_mb: float, cache_dir: Path):
    """
    Generate a corpus using a streaming/generator approach to avoid loading everything in memory.

    Yields batches of documents instead of returning a full list.

    Args:
        target_size_mb: Target size in megabytes
        cache_dir: Directory to cache downloaded texts

    Yields:
        Batches of (author, title, text) tuples
    """
    cache_dir.mkdir(parents=True, exist_ok=True)

    current_size = 0
    target_bytes = int(target_size_mb * 1024 * 1024)
    total_docs = 0

    # Download texts until we reach target size
    for ebook_id, author, title in GUTENBERG_TEXTS:
        if current_size >= target_bytes:
            break

        author_clean, title_clean, text = download_gutenberg_text(ebook_id, author, title, cache_dir)

        if text:
            # Split large texts into chapters/sections for more realistic documents
            sections = split_into_sections(text, author_clean, title_clean)

            text_size = sum(len(a) + len(t) + len(txt) for a, t, txt in sections)
            current_size += text_size
            total_docs += len(sections)

            print(f"  Added {len(sections)} sections, {text_size / (1024*1024):.2f} MB")
            print(f"  Total size: {current_size / (1024*1024):.2f} MB / {target_size_mb} MB")

            # Yield this batch
            yield sections

    # If we need more, repeat texts with chapter markers
    cycle = 1
    while current_size < target_bytes:
        for ebook_id, author, title in GUTENBERG_TEXTS:
            if current_size >= target_bytes:
                break

            cache_file = cache_dir / f"{ebook_id}.txt"
            if not cache_file.exists():
                continue

            with open(cache_file, 'r', encoding='utf-8') as f:
                text = f.read()

            sections = split_into_sections(text, f"{author} (Cycle {cycle})", title)

            text_size = sum(len(a) + len(t) + len(txt) for a, t, txt in sections)
            current_size += text_size
            total_docs += len(sections)

            # Yield this batch
            yield sections

        cycle += 1

    print(f"\nFinal corpus: {total_docs} documents, {current_size / (1024*1024):.2f} MB")


def split_into_sections(text: str, author: str, title: str, section_size: int = 2000) -> List[Tuple[str, str, str]]:
    """
    Split a large text into sections for more realistic document sizes.

    Args:
        text: The full text
        author: Author name
        title: Work title
        section_size: Target characters per section

    Returns:
        List of (author, section_title, section_text) tuples
    """
    # Try to split by chapters first
    chapter_pattern = r'(?:CHAPTER|Chapter|BOOK|Book|PART|Part|ACT|Act|SCENE|Scene)\s+(?:[IVXLCDM]+|\d+)'
    chapters = re.split(f'({chapter_pattern})', text)

    sections = []

    if len(chapters) > 3:  # If we found chapters
        current_section = ""
        chapter_num = 0

        for i, chunk in enumerate(chapters):
            if re.match(chapter_pattern, chunk):
                # This is a chapter marker
                if current_section:
                    chapter_num += 1
                    section_title = f"{title} - Section {chapter_num}"
                    sections.append((author, section_title, current_section.strip()))
                current_section = chunk
            else:
                current_section += chunk

        # Add final section
        if current_section:
            chapter_num += 1
            section_title = f"{title} - Section {chapter_num}"
            sections.append((author, section_title, current_section.strip()))

    else:
        # No clear chapters, split by size
        words = text.split()
        section_num = 0

        for i in range(0, len(words), section_size // 6):  # ~6 chars per word average
            section_words = words[i:i + section_size // 6]
            section_text = ' '.join(section_words)

            if section_text:
                section_num += 1
                section_title = f"{title} - Part {section_num}"
                sections.append((author, section_title, section_text))

    # Filter out very small sections
    sections = [(a, t, txt) for a, t, txt in sections if len(txt) > 500]

    return sections


if __name__ == "__main__":
    import sys

    # Parse size argument
    size_mb = float(sys.argv[1]) if len(sys.argv) > 1 else 10

    cache_dir = Path(__file__).parent / "gutenberg_cache"

    print(f"Generating {size_mb} MB corpus from Project Gutenberg...")
    print(f"Cache directory: {cache_dir}")
    print()

    corpus = generate_corpus(size_mb, cache_dir)

    print(f"\nGenerated {len(corpus)} documents")
    print(f"Sample documents:")
    for i, (author, title, text) in enumerate(corpus[:3]):
        print(f"\n{i+1}. {author} - {title}")
        print(f"   Length: {len(text):,} chars")
        print(f"   Preview: {text[:150].replace(chr(10), ' ')}...")
