"""Rule-based guitar parts, no LLM needed.

Useful as (1) a baseline to compare the agent against, (2) a way to test the whole
plan -> MIDI pipeline without an API key, and (3) a worked example of the part format.
"""

from __future__ import annotations

from .fretboard import find_voicings, tuning_of
from .part import SongPlan
from .theory import parse_chord, power_chord_of

STYLES = {
    "pop_strum": "acoustic strumming, open chords, D.DU.UDU",
    "ballad_arpeggio": "fingerpicked arpeggios that let chords ring",
    "travis": "Travis picking: thumb alternates bass strings, fingers fill in on top",
    "rock_power": "overdriven power chords, palm-muted eighths in quiet sections",
    "funk": "clean 16th-note scratch rhythm on small upper-string chords",
    "jazz_comp": "swung Charleston comping with compact shell voicings",
}

# pattern per energy level (low, mid, high) for 4/4; other meters fall back below
_STRUMS_44 = ("D...D.U.", "D.DU.UDU", "DUDUDUDU")
_STRUMS_34 = ("D.D.D.", "D.DUDU", "DUDUDU")
_STRUMS_68 = ("D..D..", "D.UD.U", "DUDDUD")


def _energy(plan: SongPlan, bar: int) -> float:
    sec = plan.section_at(bar)
    return float(sec.get("energy", 0.5)) if sec else 0.5


def _level(e: float) -> int:
    return 0 if e < 0.4 else (1 if e < 0.75 else 2)


def _pick_shapes(symbols, tuning, **search) -> dict[str, list]:
    """Choose one voicing per chord, keeping the hand near where it already is."""
    shapes: dict[str, list] = {}
    prev_pos = None
    for sym in symbols:
        if sym in shapes:
            continue
        chord = parse_chord(sym)
        cands = find_voicings(chord, tuning, count=8, **search)
        if not cands:
            cands = find_voicings(chord, tuning, count=8)
        if not cands:
            raise ValueError(f"no playable voicing found for {sym}")
        best = min(cands, key=lambda v: v.score + (0.5 * abs(v.position - prev_pos) if prev_pos is not None else 0))
        shapes[sym] = list(best.frets)
        prev_pos = best.position
    return shapes


def _sections(plan: SongPlan) -> list[tuple[int, int, float]]:
    """(first_bar, last_bar, energy) runs; the plan's sections or the whole song."""
    if plan.sections:
        runs = []
        for s in plan.sections:
            first = s["start_bar"]
            last = min(first + s["bars"] - 1, plan.num_bars)
            runs.append((first, last, float(s.get("energy", 0.5))))
        return runs
    return [(1, plan.num_bars, 0.5)]


def _bars_symbols(plan, first, last):
    out = []
    for b in range(first, last + 1):
        out += plan.bar_chords(b)
    return out


def generate(plan: SongPlan, style: str = "pop_strum", tuning_name: str = "standard") -> dict:
    if style not in STYLES:
        raise ValueError(f"unknown style {style!r}; choose from {', '.join(STYLES)}")
    tuning = tuning_of(tuning_name)
    num, den = plan.time_signature
    events: list[dict] = []
    all_symbols = _bars_symbols(plan, 1, plan.num_bars)
    tone = "acoustic_steel"

    if style == "pop_strum":
        shapes = _pick_shapes(all_symbols, tuning)
        table = _STRUMS_68 if (num, den) == (6, 8) else _STRUMS_34 if num == 3 else _STRUMS_44
        sub = 1 if den == 8 else 2
        if num not in (3, 4, 6):
            table = ("D" + "." * (num * sub - 1),) * 3
        for first, last, e in _sections(plan):
            events.append({"type": "strum_pattern", "bars": [first, last], "subdivision": sub,
                           "pattern": table[_level(e)], "velocity": 70 + int(25 * e),
                           "shapes": {s: shapes[s] for s in _bars_symbols(plan, first, last)}})

    elif style in ("ballad_arpeggio", "travis"):
        tone = "acoustic_nylon" if style == "ballad_arpeggio" else "acoustic_steel"
        shapes = _pick_shapes(all_symbols, tuning, min_strings=5, top_string_required=True)
        slots = num * (1 if den == 8 else 2)
        if style == "travis" and num == 4 and den == 4:
            pattern = "B+1 3 B2 2 B 1 B2 3"
        elif num == 4 and den == 4:
            pattern = "B 3 2 3 1 3 2 3"
        elif num == 3 and den == 4:
            pattern = "B 3 2 1 2 3"
        elif (num, den) == (6, 8):
            pattern = "B 3 2 1 2 3"
        else:
            pattern = " ".join((["B"] + ["3", "2", "1", "2"] * slots)[:slots])
        for first, last, e in _sections(plan):
            events.append({"type": "pick_pattern", "bars": [first, last], "subdivision": 1 if den == 8 else 2,
                           "pattern": pattern, "velocity": 60 + int(25 * e), "articulation": "let_ring",
                           "shapes": {s: shapes[s] for s in _bars_symbols(plan, first, last)}})

    elif style == "rock_power":
        tone = "distortion"
        for first, last, e in _sections(plan):
            symbols = _bars_symbols(plan, first, last)
            power = {s: power_chord_of(s) for s in symbols}
            pshapes = _pick_shapes([power[s] for s in symbols], tuning, allowed_strings={4, 5, 6})
            shapes = {s: pshapes[power[s]] for s in symbols}
            sub = 1 if den == 8 else 2
            events.append({"type": "strum_pattern", "bars": [first, last], "subdivision": sub,
                           "pattern": "D" * (num * sub),
                           "articulation": "palm_mute" if e < 0.6 else "normal",
                           "velocity": 80 + int(30 * e), "shapes": shapes})

    elif style == "funk":
        tone = "electric_clean"
        shapes = _pick_shapes(all_symbols, tuning, near_fret=7, min_strings=3, max_strings=4,
                              allowed_strings={1, 2, 3, 4})
        if (num, den) == (4, 4):
            events.append({"type": "strum_pattern", "bars": [1, plan.num_bars], "subdivision": 4,
                           "pattern": "D.XU.UXU D.XUxUXU", "velocity": 88, "shapes": shapes})
        else:
            events.append({"type": "strum_pattern", "bars": [1, plan.num_bars], "subdivision": 2,
                           "pattern": "DX" * num, "velocity": 88, "shapes": shapes})

    elif style == "jazz_comp":
        tone = "electric_jazz"
        shapes = _pick_shapes(all_symbols, tuning, near_fret=5, min_strings=3, max_strings=4)
        if (num, den) == (4, 4):
            pattern = "D..u...."  # Charleston: beat 1 and the 'and' of 2
        else:
            pattern = "D" + "." * (num * 2 - 1)
        events.append({"type": "strum_pattern", "bars": [1, plan.num_bars], "subdivision": 2,
                       "pattern": pattern, "velocity": 72, "articulation": "palm_mute", "shapes": shapes})

    # ring out on the last chord unless the style already sustains
    if style in ("pop_strum", "rock_power", "funk", "jazz_comp"):
        last_bar = plan.num_bars
        for ev in events:
            if ev["bars"][1] == last_bar:
                if ev["bars"][0] == last_bar:
                    events.remove(ev)
                else:
                    ev["bars"] = [ev["bars"][0], last_bar - 1]
                final_shape = ev["shapes"][plan.bar_chords(last_bar)[0]]
                break
        else:
            final_shape = None
        if final_shape is not None:
            events.append({"type": "strum", "bar": last_bar, "beat": 1, "duration": plan.beats_per_bar,
                           "frets": final_shape, "direction": "down", "velocity": 95})

    return {"tone": tone, "tuning": tuning_name,
            "description": f"offline '{style}' part: {STYLES[style]}", "events": events}
