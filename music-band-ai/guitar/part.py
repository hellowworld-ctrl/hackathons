"""Song plan (shared by every instrument agent) and the guitar part format.

A guitar part is JSON with a list of events. Four event types:

- strum_pattern: a strumming rhythm like "D.DU.UDU" repeated over a bar range, with a
  chord shape for each chord the plan puts in those bars.
- pick_pattern: a fingerpicking/arpeggio pattern like "B 3 2 3 1 3 2 3" over a bar range.
- strum: one strum of a chord shape at a bar/beat.
- note: one note on a string/fret at a bar/beat, with an optional technique.

Patterns keep parts short and musical (a player thinks "this groove for 8 bars"), while
single strums/notes give full control for riffs, fills and melodies.

`expand()` turns all of that into a flat list of Hits (one string plucked at one time).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .fretboard import MAX_FRET, index_string, pitch_at, shape_problems, shape_str, string_index, tuning_of
from .theory import Chord, Scale, parse_chord, parse_scale, pc_name

TONES = {
    # name -> General MIDI program (0-based)
    "acoustic_nylon": 24,
    "acoustic_steel": 25,
    "electric_jazz": 26,
    "electric_clean": 27,
    "electric_muted": 28,
    "overdrive": 29,
    "distortion": 30,
}

TECHNIQUES = {"normal", "palm_mute", "hammer_on", "pull_off", "slide", "bend", "vibrato", "harmonic", "dead"}

# natural harmonic fret -> semitones above the open string
HARMONICS = {12: 12, 7: 19, 19: 19, 5: 24, 24: 24, 4: 28, 9: 28, 16: 28}

GRID = 24  # positions snap to 1/24 of a beat (covers 32nds and triplets)


class PartError(ValueError):
    """Raised with a list of human/LLM-readable problems."""

    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


# --------------------------------------------------------------------------- song plan


@dataclass
class SongPlan:
    tempo: float
    time_signature: tuple[int, int]
    key: str
    chords: list[str]  # one entry per bar; "C G" splits the bar evenly
    title: str = "untitled"
    style: str = ""
    mood: str = ""
    feel: str = "straight"  # "straight" | "swing"
    sections: list[dict] = field(default_factory=list)
    guitar_brief: str = ""

    @property
    def beats_per_bar(self) -> int:
        return self.time_signature[0]

    @property
    def quarters_per_beat(self) -> float:
        return 4 / self.time_signature[1]

    @property
    def num_bars(self) -> int:
        return len(self.chords)

    @property
    def total_beats(self) -> float:
        return self.num_bars * self.beats_per_bar

    @property
    def scale(self) -> Scale:
        return parse_scale(self.key)

    def bar_chords(self, bar: int) -> list[str]:
        return self.chords[bar - 1].split()

    def chord_symbol_at(self, bar: int, beat: float) -> str:
        """Chord sounding at a 1-based bar and 1-based beat."""
        symbols = self.bar_chords(bar)
        span = self.beats_per_bar / len(symbols)
        idx = min(int((beat - 1) // span), len(symbols) - 1)
        return symbols[idx]

    def chord_at(self, bar: int, beat: float) -> Chord:
        return parse_chord(self.chord_symbol_at(bar, beat))

    def chord_segments(self) -> list[tuple[float, float, str]]:
        """(start, end, symbol) in absolute beats from song start (0-based)."""
        segs = []
        for bar in range(1, self.num_bars + 1):
            symbols = self.bar_chords(bar)
            span = self.beats_per_bar / len(symbols)
            for i, sym in enumerate(symbols):
                start = (bar - 1) * self.beats_per_bar + i * span
                segs.append((start, start + span, sym))
        return segs

    def section_at(self, bar: int) -> dict | None:
        for s in self.sections:
            if s["start_bar"] <= bar < s["start_bar"] + s["bars"]:
                return s
        return None

    @classmethod
    def from_dict(cls, d: dict) -> "SongPlan":
        problems = []
        for key in ("tempo", "time_signature", "key", "chords"):
            if key not in d:
                problems.append(f"song plan is missing '{key}'")
        if problems:
            raise PartError(problems)
        ts = d["time_signature"]
        if isinstance(ts, str):
            ts = [int(x) for x in ts.split("/")]
        plan = cls(
            tempo=float(d["tempo"]),
            time_signature=(int(ts[0]), int(ts[1])),
            key=d["key"],
            chords=[str(c) for c in d["chords"]],
            title=d.get("title", "untitled"),
            style=d.get("style", ""),
            mood=d.get("mood", ""),
            feel=d.get("feel", "straight"),
            sections=d.get("sections", []),
            guitar_brief=d.get("guitar_brief", ""),
        )
        plan.scale  # validate
        for bar in range(1, plan.num_bars + 1):
            for sym in plan.bar_chords(bar):
                try:
                    parse_chord(sym)
                except ValueError as e:
                    problems.append(f"bar {bar}: {e}")
        if problems:
            raise PartError(problems)
        return plan

    @classmethod
    def load(cls, path: str | Path) -> "SongPlan":
        return cls.from_dict(json.loads(Path(path).read_text()))

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "tempo": self.tempo,
            "time_signature": list(self.time_signature),
            "key": self.key,
            "style": self.style,
            "mood": self.mood,
            "feel": self.feel,
            "sections": self.sections,
            "chords": self.chords,
            "guitar_brief": self.guitar_brief,
        }


# --------------------------------------------------------------------------- hits


@dataclass
class Hit:
    """One string sounding once. Times are in beats from the start of the song (0-based)."""

    start: float
    duration: float
    string: int
    fret: int
    velocity: int
    technique: str = "normal"
    slide_from: int | None = None
    bend: float = 0.0
    strum_id: int | None = None  # hits from one strum share an id
    strum_index: int = 0  # order the pick crosses the strings in
    direction: str = ""  # "down" / "up" for strum hits
    source: str = ""  # which event produced this, for error messages

    def pitch(self, tuning: tuple[int, ...]) -> int:
        if self.technique == "harmonic":
            return pitch_at(tuning, self.string, 0) + HARMONICS[self.fret]
        return pitch_at(tuning, self.string, self.fret)


@dataclass
class GuitarPart:
    tone: str
    tuning: str
    events: list[dict]
    description: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "GuitarPart":
        problems = []
        tone = d.get("tone", "acoustic_steel")
        if tone not in TONES:
            problems.append(f"tone {tone!r} unknown; use one of {', '.join(TONES)}")
        tuning = d.get("tuning", "standard")
        try:
            tuning_of(tuning)
        except ValueError as e:
            problems.append(str(e))
        events = d.get("events")
        if not isinstance(events, list) or not events:
            problems.append("'events' must be a non-empty list")
        elif not all(isinstance(e, dict) for e in events):
            problems.append("every entry in 'events' must be an object")
        if problems:
            raise PartError(problems)
        return cls(tone=tone, tuning=tuning, events=events, description=d.get("description", ""))

    def to_dict(self) -> dict:
        return {"tone": self.tone, "tuning": self.tuning, "description": self.description,
                "events": self.events}


# --------------------------------------------------------------------------- expansion

STRUM_CHARS = {"D": ("down", 0), "U": ("up", 0), "d": ("down", -25), "u": ("up", -25),
               "X": ("dead", 0), "x": ("dead", -15)}
REST_CHARS = {".", "-"}


def _label(i: int, ev: dict) -> str:
    kind = ev.get("type", "?")
    if "bars" in ev:
        return f"events[{i}] ({kind} bars {ev.get('bars')})"
    return f"events[{i}] ({kind} bar {ev.get('bar')} beat {ev.get('beat')})"


def _snap(x: float) -> float:
    return round(x * GRID) / GRID


def _bar_range(ev, plan, label, problems) -> tuple[int, int] | None:
    bars = ev.get("bars")
    if (not isinstance(bars, list) or len(bars) != 2
            or not all(isinstance(b, int) for b in bars)):
        problems.append(f"{label}: 'bars' must be [first_bar, last_bar] (inclusive, 1-based)")
        return None
    a, b = bars
    if not (1 <= a <= b <= plan.num_bars):
        problems.append(f"{label}: bars {a}-{b} outside the song (1-{plan.num_bars})")
        return None
    return a, b


def _shapes(ev, plan, first, last, label, problems) -> dict[str, list] | None:
    shapes = ev.get("shapes")
    if not isinstance(shapes, dict):
        problems.append(f"{label}: 'shapes' must map chord symbol -> 6 frets (low E -> high E)")
        return None
    needed = []
    for bar in range(first, last + 1):
        for sym in plan.bar_chords(bar):
            if sym not in needed:
                needed.append(sym)
    missing = [s for s in needed if s not in shapes]
    if missing:
        problems.append(
            f"{label}: no shape for chord(s) {', '.join(missing)} which the plan uses in these bars; "
            f"call get_chord_voicings and add them to 'shapes'"
        )
    ok = True
    for sym, frets in shapes.items():
        if not isinstance(frets, list):
            problems.append(f"{label}: shape for {sym} must be a list of 6 frets or nulls")
            ok = False
            continue
        for p in shape_problems(frets):
            problems.append(f"{label}: shape {sym} {frets}: {p}")
            ok = False
    return shapes if ok and not missing else None


def _check_shape_matches_chord(frets, sym, tuning, label, warnings):
    """Warn when a shape doesn't spell the chord it is assigned to."""
    try:
        chord = parse_chord(sym)
    except ValueError:
        return
    sounding = [tuning[i] + f for i, f in enumerate(frets) if f is not None]
    pcs = {p % 12 for p in sounding}
    missing = chord.essential - pcs
    foreign = pcs - chord.pcs
    if pcs == {chord.root, (chord.root + 7) % 12} and not foreign:
        return  # a power chord is a fine guitar reading of any chord with a perfect fifth
    if missing:
        warnings.append(f"{label}: shape {shape_str(frets)} for {sym} is missing "
                        f"{', '.join(pc_name(p) for p in sorted(missing))}")
    if foreign:
        warnings.append(f"{label}: shape {shape_str(frets)} for {sym} adds "
                        f"{', '.join(pc_name(p) for p in sorted(foreign))} (not in the chord)")


def _strum_hits(frets, start, duration, direction, velocity, articulation, strum_id, source):
    """One strum across a shape -> hits in pick order."""
    idx = [i for i, f in enumerate(frets) if f is not None]
    if direction == "up":
        idx = list(reversed(idx))[:4]  # up-strums mostly catch the top strings
    hits = []
    for order, i in enumerate(idx):
        tech = {"palm_mute": "palm_mute", "dead": "dead"}.get(articulation, "normal")
        # the pick digs into the first strings it hits a little harder
        vel = velocity - 2 * order
        hits.append(Hit(start=start, duration=duration, string=index_string(i), fret=frets[i],
                        velocity=vel, technique=tech, strum_id=strum_id, strum_index=order,
                        direction=direction, source=source))
    return hits


def expand(part: GuitarPart, plan: SongPlan) -> tuple[list[Hit], list[str], list[str]]:
    """Part events -> flat list of hits. Returns (hits, errors, warnings)."""
    tuning = tuning_of(part.tuning)
    hits: list[Hit] = []
    problems: list[str] = []
    warnings: list[str] = []
    bpb = plan.beats_per_bar
    strum_id = 0
    checked_shapes: set[tuple] = set()
    seg_ends = sorted({end for _, end, _ in plan.chord_segments()})

    def chord_end(t: float) -> float:
        for end in seg_ends:
            if end > t + 1e-9:
                return end
        return plan.total_beats

    for i, ev in enumerate(part.events):
        kind = ev.get("type")
        label = _label(i, ev)
        velocity = ev.get("velocity", 85)
        if not isinstance(velocity, (int, float)) or not 1 <= velocity <= 127:
            problems.append(f"{label}: velocity must be 1-127")
            continue
        velocity = int(velocity)

        if kind in ("strum_pattern", "pick_pattern"):
            rng = _bar_range(ev, plan, label, problems)
            sub = ev.get("subdivision", 2)
            if not isinstance(sub, int) or sub not in (1, 2, 3, 4, 6):
                problems.append(f"{label}: subdivision must be 1, 2, 3, 4 or 6 slots per beat")
                continue
            if rng is None:
                continue
            first, last = rng
            shapes = _shapes(ev, plan, first, last, label, problems)
            if shapes is None:
                continue
            for sym, frets in shapes.items():
                key = (sym, tuple(frets))
                if key not in checked_shapes:
                    checked_shapes.add(key)
                    _check_shape_matches_chord(frets, sym, tuning, label, warnings)
            slots_per_bar = bpb * sub
            slot = 1 / sub
            articulation = ev.get("articulation", "normal")
            if articulation not in ("normal", "palm_mute", "let_ring"):
                problems.append(f"{label}: articulation must be normal, palm_mute or let_ring")
                continue

            pattern = ev.get("pattern")
            if not isinstance(pattern, str) or not pattern.strip():
                problems.append(f"{label}: 'pattern' must be a non-empty string")
                continue

            if kind == "strum_pattern":
                cells = [c for c in pattern if c not in " |"]
                bad = sorted({c for c in cells if c not in STRUM_CHARS and c not in REST_CHARS})
                if bad:
                    problems.append(f"{label}: pattern characters {bad} not allowed; "
                                    f"use D U d u X x and . for no strum")
                    continue
            else:
                cells = pattern.replace("|", " ").split()
            if len(cells) % slots_per_bar:
                problems.append(
                    f"{label}: pattern has {len(cells)} slots but a bar has {slots_per_bar} "
                    f"({bpb} beats x subdivision {sub}); length must be a multiple of {slots_per_bar}")
                continue

            base = (first - 1) * bpb
            n_slots = (last - first + 1) * slots_per_bar
            attack_slots = [k for k in range(n_slots) if cells[k % len(cells)] not in REST_CHARS]
            for j, k in enumerate(attack_slots):
                t = base + k * slot
                bar = int(t // bpb) + 1
                beat = t - (bar - 1) * bpb + 1
                sym = plan.chord_symbol_at(bar, beat)
                frets = shapes[sym]
                nxt = base + attack_slots[j + 1] * slot if j + 1 < len(attack_slots) else base + n_slots * slot
                cell = cells[k % len(cells)]
                src = f"{label} bar {bar} beat {beat:g}"
                if kind == "strum_pattern":
                    direction, accent = STRUM_CHARS[cell]
                    art = "dead" if direction == "dead" else articulation
                    # a strum rings until the next strum; a chord change lifts the fingers
                    dur = min(nxt, chord_end(t)) - t if art != "let_ring" else chord_end(t) - t
                    hits.extend(_strum_hits(frets, t, dur, "down" if direction == "dead" else direction,
                                            velocity + accent, art, strum_id, src))
                    strum_id += 1
                else:
                    hits.extend(_pick_hits(cell, frets, t, slot, nxt, chord_end(t), velocity,
                                           articulation, src, problems, sym))

        elif kind == "strum":
            pos = _position(ev, plan, label, problems)
            frets = ev.get("frets")
            if pos is None:
                continue
            if not isinstance(frets, list):
                problems.append(f"{label}: 'frets' must be 6 frets or nulls, low E -> high E")
                continue
            bad = shape_problems(frets)
            if bad:
                problems.extend(f"{label}: shape {frets}: {p}" for p in bad)
                continue
            direction = ev.get("direction", "down")
            if direction not in ("down", "up"):
                problems.append(f"{label}: direction must be down or up")
                continue
            articulation = ev.get("articulation", "normal")
            if articulation not in ("normal", "palm_mute", "dead"):
                problems.append(f"{label}: articulation must be normal, palm_mute or dead")
                continue
            dur = _duration(ev, label, problems)
            if dur is None:
                continue
            t, bar, beat = pos
            sym = plan.chord_symbol_at(bar, beat)
            key = (sym, tuple(frets))
            if key not in checked_shapes and articulation != "dead":
                checked_shapes.add(key)
                _check_shape_matches_chord(frets, sym, tuning, label, warnings)
            hits.extend(_strum_hits(frets, t, dur, direction, velocity, articulation, strum_id, label))
            strum_id += 1

        elif kind == "note":
            pos = _position(ev, plan, label, problems)
            dur = _duration(ev, label, problems)
            string, fret = ev.get("string"), ev.get("fret")
            tech = ev.get("technique", "normal")
            if pos is None or dur is None:
                continue
            if string not in (1, 2, 3, 4, 5, 6):
                problems.append(f"{label}: string must be 1 (high E) to 6 (low E)")
                continue
            if not isinstance(fret, int) or not 0 <= fret <= MAX_FRET:
                problems.append(f"{label}: fret must be 0-{MAX_FRET}")
                continue
            if tech not in TECHNIQUES:
                problems.append(f"{label}: technique must be one of {', '.join(sorted(TECHNIQUES))}")
                continue
            hit = Hit(start=pos[0], duration=dur, string=string, fret=fret, velocity=velocity,
                      technique=tech, source=label)
            if tech == "slide":
                sf = ev.get("slide_from")
                if not isinstance(sf, int) or not 0 <= sf <= MAX_FRET or sf == fret:
                    problems.append(f"{label}: slide needs 'slide_from' (a different fret 0-{MAX_FRET})")
                    continue
                hit.slide_from = sf
            if tech == "bend":
                b = ev.get("bend", 2)
                if not isinstance(b, (int, float)) or not 0 < b <= 2:
                    problems.append(f"{label}: bend must be 0.5, 1 or 2 semitones")
                    continue
                hit.bend = float(b)
            if tech == "harmonic" and fret not in HARMONICS:
                problems.append(f"{label}: natural harmonics ring at frets {sorted(HARMONICS)}")
                continue
            hits.append(hit)
        else:
            problems.append(f"{label}: type must be strum_pattern, pick_pattern, strum or note")

    hits.sort(key=lambda h: (h.start, h.strum_index))
    return hits, problems, warnings


PICK_TOKEN = re.compile(r"^(B2|B|[1-6])$")


def _pick_hits(token, frets, t, slot, nxt, chord_end, velocity, articulation, src, problems, sym):
    sounding = [i for i, f in enumerate(frets) if f is not None]
    out = []
    for part in token.split("+"):
        if not PICK_TOKEN.match(part):
            problems.append(f"{src}: pick token {part!r} must be B (bass), B2 (next string up), "
                            f"a string number 1-6, or '.' for rest; join simultaneous notes with +")
            return []
        if part == "B":
            i = sounding[0]
        elif part == "B2":
            if len(sounding) < 2:
                problems.append(f"{src}: B2 needs two sounding strings in the {sym} shape")
                return []
            i = sounding[1]
        else:
            i = string_index(int(part))
            if frets[i] is None:
                problems.append(f"{src}: pattern picks string {part} but the {sym} shape "
                                f"{shape_str(frets)} mutes it")
                return []
        if articulation == "palm_mute":
            dur, tech = slot, "palm_mute"
        elif articulation == "normal":
            dur, tech = nxt - t, "normal"
        else:  # let_ring: sustain until the chord changes (or the string is picked again)
            dur, tech = chord_end - t, "normal"
        # thumb (bass) a touch louder than fingers
        vel = velocity + (6 if part in ("B", "B2") else 0)
        out.append(Hit(start=t, duration=max(dur, slot), string=index_string(i), fret=frets[i],
                       velocity=min(vel, 127), technique=tech, source=src))
    return out


def _position(ev, plan, label, problems) -> tuple[float, int, float] | None:
    bar, beat = ev.get("bar"), ev.get("beat", 1)
    if not isinstance(bar, int) or not 1 <= bar <= plan.num_bars:
        problems.append(f"{label}: bar must be 1-{plan.num_bars}")
        return None
    if not isinstance(beat, (int, float)) or not 1 <= beat < plan.beats_per_bar + 1:
        problems.append(f"{label}: beat must be >= 1 and < {plan.beats_per_bar + 1} "
                        f"(1-based, e.g. 2.5 = the 'and' of 2)")
        return None
    beat = _snap(beat)
    return (bar - 1) * plan.beats_per_bar + beat - 1, bar, beat


def _duration(ev, label, problems) -> float | None:
    dur = ev.get("duration", 1)
    if not isinstance(dur, (int, float)) or dur <= 0:
        problems.append(f"{label}: duration must be > 0 beats")
        return None
    return max(_snap(dur), 1 / GRID)
