"""Checks a guitar part against the song plan and against what two hands can play.

Errors mean the part can't be rendered as written (the agent must fix them).
Warnings are musical smells worth a second look (the agent may keep them).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .fretboard import MAX_SPAN, shape_problems, string_index, tuning_of
from .part import GuitarPart, Hit, PartError, SongPlan, expand
from .theory import note_name, pc_name

MAX_LISTED = 20


@dataclass
class Validation:
    hits: list[Hit] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def report(self) -> str:
        lines = []
        if self.errors:
            lines.append(f"{len(self.errors)} error(s) - the part was NOT accepted:")
            lines += [f"  - {e}" for e in _cap(self.errors)]
        if self.warnings:
            lines.append(f"{len(self.warnings)} warning(s):")
            lines += [f"  - {w}" for w in _cap(self.warnings)]
        if self.stats:
            s = self.stats
            lines.append(
                f"stats: {s['hits']} notes, bars with guitar {s['bars_played']}/{s['bars_total']}, "
                f"range {s['lowest']} - {s['highest']}"
            )
        return "\n".join(lines) or "ok"


def _cap(items: list[str]) -> list[str]:
    if len(items) <= MAX_LISTED:
        return items
    return items[:MAX_LISTED] + [f"... and {len(items) - MAX_LISTED} more like these"]


def _where(plan: SongPlan, t: float) -> str:
    bar = int(t // plan.beats_per_bar) + 1
    beat = t - (bar - 1) * plan.beats_per_bar + 1
    return f"bar {bar} beat {beat:g}"


def validate(part_data: dict | GuitarPart, plan: SongPlan) -> Validation:
    v = Validation()
    try:
        part = part_data if isinstance(part_data, GuitarPart) else GuitarPart.from_dict(part_data)
    except PartError as e:
        v.errors = e.problems
        return v

    hits, errors, warnings = expand(part, plan)
    v.errors += errors
    v.warnings += warnings
    if errors:
        return v

    tuning = tuning_of(part.tuning)
    _check_simultaneous(hits, plan, v)
    _check_jumps(hits, plan, v)
    _check_harmony(hits, plan, tuning, v)
    _check_density(hits, plan, v)

    for h in hits:  # nothing rings past the last bar
        h.duration = min(h.duration, plan.total_beats - h.start)
    v.hits = hits

    if hits:
        pitches = [h.pitch(tuning) for h in hits]
        bars = {int(h.start // plan.beats_per_bar) + 1 for h in hits}
        v.stats = {
            "hits": len(hits),
            "bars_played": len(bars),
            "bars_total": plan.num_bars,
            "lowest": note_name(min(pitches)),
            "highest": note_name(max(pitches)),
        }
        silent = [b for b in range(1, plan.num_bars + 1) if b not in bars]
        if silent and len(silent) > plan.num_bars // 2:
            v.warnings.append(f"guitar is silent in {len(silent)} of {plan.num_bars} bars "
                              f"(e.g. bars {', '.join(map(str, silent[:8]))})")
    else:
        v.errors.append("the part contains no notes")
    return v


def _check_simultaneous(hits: list[Hit], plan: SongPlan, v: Validation) -> None:
    """Everything that starts together must fit one fretting hand, one note per string."""
    by_time: dict[float, list[Hit]] = defaultdict(list)
    for h in hits:
        by_time[round(h.start, 4)].append(h)
    for t, group in by_time.items():
        if len(group) < 2:
            continue
        frets: list[int | None] = [None] * 6
        clash = False
        for h in group:
            i = string_index(h.string)
            if frets[i] is not None and frets[i] != h.fret:
                v.errors.append(
                    f"{_where(plan, t)}: string {h.string} asked to play fret {frets[i]} and "
                    f"fret {h.fret} at the same time ({h.source})")
                clash = True
            frets[i] = 0 if h.technique == "harmonic" else h.fret
        if clash:
            continue
        # strum shapes were already checked; this catches notes/double-stops/overlaps
        if any(h.strum_id is None for h in group):
            for p in shape_problems(frets):
                if "muted" not in p:
                    v.errors.append(f"{_where(plan, t)}: notes played together - {p}")


def _check_jumps(hits: list[Hit], plan: SongPlan, v: Validation) -> None:
    singles = [h for h in hits if h.strum_id is None and h.fret > 0 and h.technique != "harmonic"]
    for a, b in zip(singles, singles[1:]):
        gap = b.start - a.start
        if 0 < gap <= 0.25 and abs(b.fret - a.fret) > MAX_SPAN + 3:
            v.warnings.append(
                f"{_where(plan, b.start)}: jump from fret {a.fret} to {b.fret} in "
                f"{gap:g} beats is a very fast shift - consider an open string or a closer position")


def _check_harmony(hits: list[Hit], plan: SongPlan, tuning, v: Validation) -> None:
    scale_pcs = plan.scale.pcs
    for h in hits:
        if h.strum_id is not None:
            continue
        on_beat = abs(h.start - round(h.start)) < 1e-6
        if not on_beat or h.duration < 0.5:
            continue  # passing tones on weak beats are fine
        bar = int(h.start // plan.beats_per_bar) + 1
        beat = h.start - (bar - 1) * plan.beats_per_bar + 1
        chord = plan.chord_at(bar, beat)
        pitch = h.pitch(tuning) + (int(round(h.bend)) if h.technique == "bend" else 0)
        pc = pitch % 12
        if pc not in chord.pcs and pc not in scale_pcs:
            v.warnings.append(
                f"{_where(plan, h.start)}: {note_name(pitch)} (string {h.string} fret {h.fret}) "
                f"is outside both {chord.symbol} and {plan.key} on a strong beat")
        elif pc not in chord.pcs and h.duration >= 2:
            v.warnings.append(
                f"{_where(plan, h.start)}: long {pc_name(pc)} held over {chord.symbol} "
                f"is not a chord tone - intended as a suspension?")


def _check_density(hits: list[Hit], plan: SongPlan, v: Validation) -> None:
    per_bar: dict[int, int] = defaultdict(int)
    for h in hits:
        if h.strum_id is None:
            per_bar[int(h.start // plan.beats_per_bar) + 1] += 1
    busy = [b for b, n in sorted(per_bar.items()) if n > 4 * plan.beats_per_bar]
    if busy:
        v.warnings.append(f"very busy single-note writing (more than 16ths) in bars "
                          f"{', '.join(map(str, busy[:8]))} - leave space for the band")
