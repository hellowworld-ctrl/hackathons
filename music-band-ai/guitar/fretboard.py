"""The physical guitar: tunings, what a hand can actually hold, and chord voicing search.

Conventions used everywhere in this package:
- Strings are numbered like guitarists do: 6 = low E ... 1 = high E.
- A chord shape is a list of 6 frets ordered low E -> high E (same as chord charts,
  "x32010" = [None, 3, 2, 0, 1, 0]). None = muted/not played, 0 = open string.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

from .theory import Chord, Scale, note_name, pc_name

TUNINGS: dict[str, tuple[int, ...]] = {
    "standard": (40, 45, 50, 55, 59, 64),
    "drop_d": (38, 45, 50, 55, 59, 64),
    "half_step_down": (39, 44, 49, 54, 58, 63),
    "open_g": (38, 43, 50, 55, 59, 62),
    "dadgad": (38, 45, 50, 55, 57, 62),
}

MAX_FRET = 22
MAX_FINGERS = 4
MAX_SPAN = 4  # fretted notes held at once may cover at most 5 frets (e.g. 3..7)

Shape = list  # list[int | None], length 6, low E -> high E


def tuning_of(name: str) -> tuple[int, ...]:
    if name not in TUNINGS:
        raise ValueError(f"unknown tuning {name!r}; supported: {', '.join(TUNINGS)}")
    return TUNINGS[name]


def string_index(string: int) -> int:
    """Guitar string number (6..1) -> index into a low-to-high shape (0..5)."""
    return 6 - string


def index_string(index: int) -> int:
    return 6 - index


def pitch_at(tuning: tuple[int, ...], string: int, fret: int) -> int:
    return tuning[string_index(string)] + fret


def shape_str(frets: Shape) -> str:
    """[None, 3, 2, 0, 1, 0] -> 'x32010'; uses dashes once any fret has two digits."""
    parts = ["x" if f is None else str(f) for f in frets]
    return "-".join(parts) if any(len(p) > 1 for p in parts) else "".join(parts)


def shape_pitches(frets: Shape, tuning: tuple[int, ...]) -> list[int]:
    return [tuning[i] + f for i, f in enumerate(frets) if f is not None]


def fingers_needed(frets: Shape) -> tuple[int, bool]:
    """How many fretting fingers a shape needs, and whether it uses a barre.

    A barre is possible when the lowest fretted fret appears on 2+ strings and every
    string between the outermost of those is fretted at or above it (no open strings
    under the barre)."""
    fretted = [(i, f) for i, f in enumerate(frets) if f]
    if not fretted:
        return 0, False
    low = min(f for _, f in fretted)
    at_low = [i for i, f in fretted if f == low]
    plain = len(fretted)
    if len(at_low) >= 2:
        lo_i, hi_i = min(at_low), max(at_low)
        covered = all(frets[i] is None or frets[i] >= low for i in range(lo_i, hi_i + 1))
        opens_under = any(frets[i] == 0 for i in range(lo_i, hi_i + 1))
        if covered and not opens_under:
            barred = 1 + sum(1 for _, f in fretted if f != low)
            if barred < plain:
                return barred, True
    return plain, False


def shape_problems(frets: Shape) -> list[str]:
    """Reasons a hand can't hold this shape. Empty list = playable."""
    problems = []
    if len(frets) != 6:
        return [f"needs 6 entries (low E -> high E), got {len(frets)}"]
    for f in frets:
        if f is not None and (not isinstance(f, int) or f < 0 or f > MAX_FRET):
            problems.append(f"fret {f!r} is not 0..{MAX_FRET} or null")
    if problems:
        return problems
    fretted = [f for f in frets if f]
    if fretted and max(fretted) - min(fretted) > MAX_SPAN:
        problems.append(
            f"stretch from fret {min(fretted)} to {max(fretted)} is wider than {MAX_SPAN + 1} frets"
        )
    fingers, _ = fingers_needed(frets)
    if fingers > MAX_FINGERS:
        problems.append(f"needs {fingers} fretting fingers (max {MAX_FINGERS}, barre counts as 1)")
    if all(f is None for f in frets):
        problems.append("all strings muted")
    return problems


@dataclass
class Voicing:
    frets: tuple
    pitches: list[int]
    fingers: int
    barre: bool
    position: int  # lowest fretted fret, 0 for all-open shapes
    score: float

    @property
    def name(self) -> str:
        return shape_str(list(self.frets))

    def describe(self) -> str:
        notes = " ".join(note_name(p) for p in self.pitches)
        where = "open position" if self.position <= 3 and 0 in self.frets else f"fret {self.position}"
        barre = ", barre" if self.barre else ""
        return f"{self.name:<18} {where}, {self.fingers} fingers{barre}; notes {notes}"


def find_voicings(
    chord: Chord,
    tuning: tuple[int, ...] = TUNINGS["standard"],
    count: int = 5,
    near_fret: int | None = None,
    min_strings: int = 4,
    max_strings: int = 6,
    top_string_required: bool = False,
    allowed_strings: set[int] | None = None,
    max_position: int = 12,
) -> list[Voicing]:
    """Search the fretboard for playable voicings of a chord, best first.

    Hard rules: lowest note is the chord's bass, every essential tone is present,
    hand can hold it (span/fingers). Scoring prefers full, resonant, easy shapes
    close to `near_fret` when given."""
    if chord.is_power_chord:
        min_strings = min(min_strings, 2)
        max_strings = min(max_strings, 3)
    pcs = chord.pcs
    seen: set[tuple] = set()
    results: list[Voicing] = []

    for pos in range(0, max_position + 1):
        window = range(max(1, pos), pos + MAX_SPAN)  # 4-fret window + open strings
        options = []
        for i, open_pitch in enumerate(tuning):
            string = index_string(i)
            opts: list[int | None] = [None]
            if allowed_strings is None or string in allowed_strings:
                if open_pitch % 12 in pcs:
                    opts.append(0)
                opts.extend(f for f in window if (open_pitch + f) % 12 in pcs)
            options.append(opts)

        for frets in itertools.product(*options):
            if frets in seen:
                continue
            seen.add(frets)
            sounding = [i for i, f in enumerate(frets) if f is not None]
            if not (min_strings <= len(sounding) <= max_strings):
                continue
            if top_string_required and frets[5] is None:
                continue
            pitches = [tuning[i] + frets[i] for i in sounding]
            if pitches[0] % 12 != chord.bass:
                continue
            if not chord.essential <= {p % 12 for p in pitches}:
                continue
            if shape_problems(list(frets)):
                continue
            results.append(_score(chord, frets, pitches, sounding, near_fret))

    results.sort(key=lambda v: v.score)
    return results[:count]


def _score(chord, frets, pitches, sounding, near_fret) -> Voicing:
    fingers, barre = fingers_needed(list(frets))
    fretted = [f for f in frets if f]
    position = min(fretted) if fretted else 0
    opens = sum(1 for f in frets if f == 0)
    interior_mutes = sum(
        1 for i in range(sounding[0], sounding[-1] + 1) if frets[i] is None
    )
    has_fifth = (chord.root + 7) % 12 in {p % 12 for p in pitches}
    top = max(fretted) if fretted else 0
    # the lowest two notes a third apart sound muddy on guitar
    muddy = len(pitches) > 1 and pitches[0] < 52 and pitches[1] - pitches[0] < 5
    # the same pitch on two strings (fretted D5 + open D) wastes a string
    unisons = len(pitches) - len(set(pitches))

    score = -1.5 * len(sounding)
    score += 0.5 * fingers
    score += 1.0 if barre else 0.0
    score += 4.0 * interior_mutes
    score += 0.0 if has_fifth or chord.intervals == (0, 7) else 0.5
    score += 1.5 if muddy else 0.0
    score += 2.5 * unisons
    score += 1.0 * opens if top > 5 else 0.0  # open strings under a high hand ring oddly
    if near_fret is None:
        score += 0.4 * top
        score -= 0.8 * opens if top <= 5 else 0.0
    else:
        center = (min(fretted) + max(fretted)) / 2 if fretted else 0
        score += 1.0 * abs(center - near_fret)
    return Voicing(frets=frets, pitches=pitches, fingers=fingers, barre=barre,
                   position=position, score=round(score, 2))


def scale_map(scale: Scale, tuning: tuple[int, ...], position: int, width: int = 5) -> str:
    """ASCII fretboard of scale tones in a position. 'R' marks roots."""
    lo = max(0, position)
    hi = lo + width - 1
    frets = list(range(lo, hi + 1))
    header = "      " + "".join(f"{f:>4}" for f in frets)
    lines = [f"{scale.name} around fret {position} (R = root; number = fret to play)", header]
    for string in range(1, 7):
        open_pitch = tuning[string_index(string)]
        row = []
        for f in frets:
            pc = (open_pitch + f) % 12
            if pc == scale.root:
                row.append("   R")
            elif pc in scale.pcs:
                row.append(f"{f:>4}")
            else:
                row.append("   .")
        label = pc_name(open_pitch % 12)
        lines.append(f"{string} ({label:<2})" + "".join(row))
    return "\n".join(lines)
