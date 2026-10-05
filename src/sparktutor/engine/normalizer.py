"""Answer normalization for comparison."""

from __future__ import annotations

import re
import unicodedata


_FULLWIDTH_ASCII = {code: code - 0xFEE0 for code in range(0xFF01, 0xFF5F)}
_FULLWIDTH_ASCII[0x3000] = ord(" ")


def normalize_text(text: str) -> str:
    """Normalize text for comparison: strip, collapse whitespace, lowercase."""
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    return text.lower()


def normalize_code(code: str) -> str:
    """Normalize code for comparison: strip, normalize quotes and whitespace."""
    code = code.strip()
    code = re.sub(r'["\']', "'", code)
    code = re.sub(r"\s+", " ", code)
    return code


def normalize_choice(choice: str) -> str:
    """Keep Unicode text and meaningful code punctuation when comparing choices.

    Only fold full-width ASCII, whitespace and letter case. Unlike blanket NFKC
    normalization, this does not turn mathematical symbols such as x² into x2.
    """
    choice = unicodedata.normalize("NFC", choice).translate(_FULLWIDTH_ASCII)
    return re.sub(r"\s+", " ", choice.strip()).casefold()


def choices_match(guess: str, correct: str) -> bool:
    """Check if a multiple-choice guess matches the correct answer."""
    normalized_guess = normalize_choice(guess)
    return bool(normalized_guess) and normalized_guess == normalize_choice(correct)


def code_match(guess: str, correct: str) -> bool:
    """Check if code guess matches the expected answer (flexible)."""
    return normalize_code(guess) == normalize_code(correct)
