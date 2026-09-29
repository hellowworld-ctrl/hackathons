"""Pitch, chord and scale helpers shared by the guitar agent.

Pitches are MIDI numbers (60 = C4). Pitch classes are 0-11 with C = 0.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

NOTE_NAMES_SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
NOTE_NAMES_FLAT = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]

_LETTER_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def parse_pitch_class(name: str) -> int:
    """'C' -> 0, 'F#' -> 6, 'Bb' -> 10."""
    name = name.strip()
    if not name or name[0].upper() not in _LETTER_PC:
        raise ValueError(f"not a note name: {name!r}")
    pc = _LETTER_PC[name[0].upper()]
    for accidental in name[1:]:
        if accidental in "#♯":
            pc += 1
        elif accidental in "b♭":
            pc -= 1
        else:
            raise ValueError(f"not a note name: {name!r}")
    return pc % 12


def note_name(pitch: int, flats: bool = False) -> str:
    """MIDI pitch -> 'C4' style name."""
    names = NOTE_NAMES_FLAT if flats else NOTE_NAMES_SHARP
    return f"{names[pitch % 12]}{pitch // 12 - 1}"


def pc_name(pc: int, flats: bool = False) -> str:
    return (NOTE_NAMES_FLAT if flats else NOTE_NAMES_SHARP)[pc % 12]


# --------------------------------------------------------------------------- chords

# Suffix -> intervals in semitones above the root. Longest suffixes are matched first.
CHORD_QUALITIES: dict[str, tuple[int, ...]] = {
    "": (0, 4, 7),
    "maj": (0, 4, 7),
    "M": (0, 4, 7),
    "m": (0, 3, 7),
    "min": (0, 3, 7),
    "-": (0, 3, 7),
    "5": (0, 7),
    "7": (0, 4, 7, 10),
    "maj7": (0, 4, 7, 11),
    "M7": (0, 4, 7, 11),
    "Δ": (0, 4, 7, 11),
    "Δ7": (0, 4, 7, 11),
    "m7": (0, 3, 7, 10),
    "min7": (0, 3, 7, 10),
    "-7": (0, 3, 7, 10),
    "mmaj7": (0, 3, 7, 11),
    "m7b5": (0, 3, 6, 10),
    "ø": (0, 3, 6, 10),
    "ø7": (0, 3, 6, 10),
    "dim": (0, 3, 6),
    "°": (0, 3, 6),
    "dim7": (0, 3, 6, 9),
    "°7": (0, 3, 6, 9),
    "aug": (0, 4, 8),
    "+": (0, 4, 8),
    "sus2": (0, 2, 7),
    "sus4": (0, 5, 7),
    "sus": (0, 5, 7),
    "7sus4": (0, 5, 7, 10),
    "7sus": (0, 5, 7, 10),
    "6": (0, 4, 7, 9),
    "m6": (0, 3, 7, 9),
    "add9": (0, 2, 4, 7),
    "madd9": (0, 2, 3, 7),
    "9": (0, 2, 4, 7, 10),
    "m9": (0, 2, 3, 7, 10),
    "maj9": (0, 2, 4, 7, 11),
    "7#9": (0, 3, 4, 7, 10),
    "7b9": (0, 1, 4, 7, 10),
}

_CHORD_RE = re.compile(r"^([A-Ga-g][#b♯♭]?)(.*?)(?:/([A-Ga-g][#b♯♭]?))?$")


@dataclass(frozen=True)
class Chord:
    symbol: str
    root: int  # pitch class
    intervals: tuple[int, ...]
    bass: int  # pitch class of the lowest note (root unless slash chord)

    @property
    def pcs(self) -> frozenset[int]:
        tones = {(self.root + i) % 12 for i in self.intervals}
        tones.add(self.bass)
        return frozenset(tones)

    @property
    def essential(self) -> frozenset[int]:
        """Tones a voicing must contain. The perfect fifth is the only tone guitarists
        routinely drop, except in power chords where it is the whole point."""
        if self.intervals == (0, 7):
            keep = self.intervals
        else:
            keep = tuple(i for i in self.intervals if i != 7)
        tones = {(self.root + i) % 12 for i in keep}
        tones.add(self.bass)
        return frozenset(tones)

    @property
    def is_power_chord(self) -> bool:
        return self.intervals == (0, 7)

    def tone_names(self) -> list[str]:
        flats = "b" in self.symbol[1:2]
        return [pc_name((self.root + i) % 12, flats) for i in self.intervals]


def parse_chord(symbol: str) -> Chord:
    """'C', 'F#m7', 'Bb/D', 'G5', 'Am7b5' -> Chord."""
    text = symbol.strip()
    m = _CHORD_RE.match(text)
    if not m:
        raise ValueError(f"can't read chord symbol {symbol!r}")
    root_txt, quality, bass_txt = m.groups()
    root = parse_pitch_class(root_txt)
    quality = quality.strip().strip("()")
    if quality not in CHORD_QUALITIES:
        raise ValueError(
            f"unknown chord quality {quality!r} in {symbol!r}; "
            f"supported: {', '.join(repr(q) for q in CHORD_QUALITIES if q)}"
        )
    bass = parse_pitch_class(bass_txt) if bass_txt else root
    return Chord(symbol=text, root=root, intervals=CHORD_QUALITIES[quality], bass=bass)


def power_chord_of(symbol: str) -> str:
    """'Am7' -> 'A5'. Used by rock styles that reduce every chord to root + fifth."""
    parse_chord(symbol)  # raises on junk
    return f"{_CHORD_RE.match(symbol.strip()).group(1)}5"


# --------------------------------------------------------------------------- scales

SCALES: dict[str, tuple[int, ...]] = {
    "major": (0, 2, 4, 5, 7, 9, 11),
    "ionian": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10),
    "aeolian": (0, 2, 3, 5, 7, 8, 10),
    "natural minor": (0, 2, 3, 5, 7, 8, 10),
    "harmonic minor": (0, 2, 3, 5, 7, 8, 11),
    "melodic minor": (0, 2, 3, 5, 7, 9, 11),
    "dorian": (0, 2, 3, 5, 7, 9, 10),
    "phrygian": (0, 1, 3, 5, 7, 8, 10),
    "lydian": (0, 2, 4, 6, 7, 9, 11),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10),
    "major pentatonic": (0, 2, 4, 7, 9),
    "minor pentatonic": (0, 3, 5, 7, 10),
    "blues": (0, 3, 5, 6, 7, 10),
}


@dataclass(frozen=True)
class Scale:
    name: str
    root: int
    intervals: tuple[int, ...]

    @property
    def pcs(self) -> frozenset[int]:
        return frozenset((self.root + i) % 12 for i in self.intervals)


def parse_scale(text: str) -> Scale:
    """'G major', 'E minor', 'Em', 'A minor pentatonic', 'D dorian', 'C'."""
    t = text.strip()
    m = re.match(r"^([A-Ga-g][#b♯♭]?)\s*(.*)$", t)
    if not m:
        raise ValueError(f"can't read key/scale {text!r}")
    root = parse_pitch_class(m.group(1))
    mode = m.group(2).strip().lower()
    if mode in ("", "maj", "major key"):
        mode = "major"
    elif mode in ("m", "min", "minor key"):
        mode = "minor"
    if mode not in SCALES:
        raise ValueError(f"unknown scale {mode!r}; supported: {', '.join(SCALES)}")
    return Scale(name=t, root=root, intervals=SCALES[mode])
