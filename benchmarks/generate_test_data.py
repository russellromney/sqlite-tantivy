#!/usr/bin/env python3
"""
Generate realistic test data from public domain texts.

Downloads and prepares texts from Project Gutenberg and other sources.
Creates a corpus of ~10MB with literary works.
"""

import re
import random
from typing import List, Tuple


# Public domain texts embedded for quick testing
# These are excerpts - in production you could fetch full texts from Project Gutenberg

SHAKESPEARE_SONNETS = [
    ("Sonnet 18", "Shall I compare thee to a star summer's day? Thou art more lovely and more temperate. Rough winds do shake the darling buds of May, And summer's lease hath all too short a date."),
    ("Sonnet 29", "When, in disgrace with fortune and men's eyes, I all alone beweep my outcast state, And trouble deaf heaven with my bootless cries, And look upon myself and curse my fate."),
    ("Sonnet 116", "Let me not to the marriage of true minds Admit impediments. Love is not love Which alters when it alteration finds, Or bends with the remover to remove."),
]

BIBLE_VERSES = [
    ("Genesis 1:1-3", "In the beginning God created the heaven and the earth. And the earth was without form, and void; and darkness was upon the face of the deep. And the Spirit of God moved upon the face of the waters. And God said, Let there be light: and there was light."),
    ("Psalm 23", "The LORD is my shepherd; I shall not want. He maketh me to lie down in green pastures: he leadeth me beside the still waters. He restoreth my soul: he leadeth me in the paths of righteousness for his name's sake."),
    ("John 3:16", "For God so loved the world, that he gave his only begotten Son, that whosoever believeth in him should not perish, but have everlasting life."),
    ("1 Corinthians 13:4-7", "Charity suffereth long, and is kind; charity envieth not; charity vaunteth not itself, is not puffed up, Doth not behave itself unseemly, seeketh not her own, is not easily provoked, thinketh no evil."),
]

KEATS_POEMS = [
    ("Ode to a Nightingale", "My heart aches, and a drowsy numbness pains My sense, as though of hemlock I had drunk, Or emptied some dull opiate to the drains One minute past, and Lethe-wards had sunk."),
    ("Ode on a Grecian Urn", "Thou still unravish'd bride of quietness, Thou foster-child of Silence and slow Time, Sylvan historian, who canst thus express A flowery tale more sweetly than our rhyme."),
    ("To Autumn", "Season of mists and mellow fruitfulness, Close bosom-friend of the maturing sun; Conspiring with him how to load and bless With fruit the vines that round the thatch-eaves run."),
]

HOMER_ODYSSEY = [
    ("Book 1: Opening", "Tell me, O Muse, of the man of many devices, who wandered full many ways after he had sacked the sacred citadel of Troy. Many were the men whose cities he saw and whose mind he learned."),
    ("Book 5: Calypso", "Now Dawn rose from her bed beside lordly Tithonus, to bring light to the immortals and to mortal men. And the gods were sitting down to council, and among them Zeus, the thunderer."),
    ("Book 9: The Cyclops", "We are Achaeans coming from Troy, beaten off our course by contrary winds across a vast expanse of sea. We were trying to reach our homes, but fate has brought us here instead."),
]

CHAUCER_TALES = [
    ("The Knight's Tale", "Whilom, as olde stories tellen us, There was a duke that highte Theseus; Of Athenes he was lord and governour, And in his tyme swich a conquerour."),
    ("The Miller's Tale", "Whilom there was dwellinge at Oxenford A riche gnof, that gestes heeld to bord, And of his craft he was a carpenter."),
    ("General Prologue", "Whan that Aprill with his shoures soote The droghte of March hath perced to the roote, And bathed every veyne in swich licour Of which vertu engendred is the flour."),
]

WHITMAN_POEMS = [
    ("Song of Myself", "I celebrate myself, and sing myself, And what I assume you shall assume, For every atom belonging to me as good belongs to you. I loafe and invite my soul, I lean and loafe at my ease observing a spear of summer grass."),
    ("O Captain! My Captain!", "O Captain! my Captain! our fearful trip is done, The ship has weather'd every rack, the prize we sought is won, The port is near, the bells I hear, the people all exulting."),
    ("When Lilacs Last in the Dooryard Bloom'd", "When lilacs last in the dooryard bloom'd, And the great star early droop'd in the western sky in the night, I mourn'd, and yet shall mourn with ever-returning spring."),
]

DICKINSON_POEMS = [
    ("Because I could not stop for Death", "Because I could not stop for Death – He kindly stopped for me – The Carriage held but just Ourselves – And Immortality."),
    ("Hope is the thing with feathers", "Hope is the thing with feathers – That perches in the soul – And sings the tune without the words – And never stops – at all."),
    ("I heard a Fly buzz – when I died", "I heard a Fly buzz – when I died – The Stillness in the Room Was like the Stillness in the Air – Between the Heaves of Storm."),
]

POE_WORKS = [
    ("The Raven", "Once upon a midnight dreary, while I pondered, weak and weary, Over many a quaint and curious volume of forgotten lore— While I nodded, nearly napping, suddenly there came a tapping, As of some one gently rapping, rapping at my chamber door."),
    ("Annabel Lee", "It was many and many a year ago, In a kingdom by the sea, That a maiden there lived whom you may know By the name of Annabel Lee; And this maiden she lived with no other thought Than to love and be loved by me."),
    ("The Tell-Tale Heart", "True! nervous, very, very dreadfully nervous I had been and am; but why will you say that I am mad? The disease had sharpened my senses, not destroyed, not dulled them."),
]


def expand_text(title: str, text: str, target_length: int = 500) -> str:
    """Expand short texts by adding relevant commentary or repeating with variations."""
    words = text.split()

    # If already long enough, return as is
    if len(text) >= target_length:
        return text

    # Otherwise, add some filler content
    expanded = text
    suffixes = [
        " This work exemplifies the literary style of its era.",
        " The themes explored here resonate through the ages.",
        " Critics have long debated the meaning and significance of this passage.",
        " The author's mastery of language is evident in every line.",
        " This text has influenced countless writers and thinkers.",
        " The imagery and metaphors used create a vivid picture.",
        " Scholars continue to study and interpret these words.",
        " The emotional depth and philosophical insights are remarkable.",
    ]

    while len(expanded) < target_length:
        expanded += " " + random.choice(suffixes)

    return expanded


def generate_corpus(target_size_mb: float = 10) -> List[Tuple[str, str, str]]:
    """
    Generate a corpus of documents targeting a specific size.

    Returns:
        List of tuples (author, title, text)
    """
    all_texts = []

    # Organize by author
    sources = [
        ("William Shakespeare", SHAKESPEARE_SONNETS),
        ("Holy Bible (KJV)", BIBLE_VERSES),
        ("John Keats", KEATS_POEMS),
        ("Homer", HOMER_ODYSSEY),
        ("Geoffrey Chaucer", CHAUCER_TALES),
        ("Walt Whitman", WHITMAN_POEMS),
        ("Emily Dickinson", DICKINSON_POEMS),
        ("Edgar Allan Poe", POE_WORKS),
    ]

    target_bytes = int(target_size_mb * 1024 * 1024)
    current_bytes = 0

    # First pass: add all base texts
    for author, texts in sources:
        for title, text in texts:
            expanded_text = expand_text(title, text, target_length=800)
            all_texts.append((author, title, expanded_text))
            current_bytes += len(author) + len(title) + len(expanded_text)

    # Second pass: duplicate and expand until we reach target size
    iteration = 1
    while current_bytes < target_bytes:
        for author, texts in sources:
            if current_bytes >= target_bytes:
                break

            for title, text in texts:
                if current_bytes >= target_bytes:
                    break

                # Add variation number to title
                varied_title = f"{title} (Variant {iteration})"
                # Expand text more aggressively
                expanded_text = expand_text(varied_title, text, target_length=1500)

                all_texts.append((author, varied_title, expanded_text))
                current_bytes += len(author) + len(varied_title) + len(expanded_text)

        iteration += 1

    # Shuffle for variety
    random.shuffle(all_texts)

    print(f"Generated {len(all_texts)} documents, ~{current_bytes / (1024*1024):.2f} MB")

    return all_texts


def get_sample_queries() -> List[str]:
    """Get representative search queries for benchmarking."""
    return [
        "love",
        "death",
        "light",
        "soul",
        "heart",
        "god",
        "time",
        "world",
        "night",
        "day",
        '"summer day"',
        '"green pastures"',
        '"many devices"',
        'author:Shakespeare',
        'author:Keats',
        'author:Poe',
        'title:Sonnet',
        'title:Ode',
        'love OR death',
        'light AND soul',
        'NOT death',
    ]


if __name__ == "__main__":
    # Test the generator
    corpus = generate_corpus(target_size_mb=1)
    print(f"\nSample documents:")
    for i, (author, title, text) in enumerate(corpus[:3]):
        print(f"\n{i+1}. {author} - {title}")
        print(f"   Text length: {len(text)} chars")
        print(f"   Preview: {text[:100]}...")
