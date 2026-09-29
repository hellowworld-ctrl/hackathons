"""ASCII guitar tab of a part, so humans (and the agent) can read what it wrote."""

from __future__ import annotations

from collections import defaultdict

from .part import Hit, SongPlan

SUFFIX = {"hammer_on": "h", "pull_off": "p", "bend": "b", "vibrato": "~", "slide": "/",
          "palm_mute": "m"}


def _cells_per_beat(hits: list[Hit]) -> int:
    """Coarsest grid that still shows every note where it is played."""
    for cpb in (1, 2, 4, 3, 6, 8, 12, 24):
        if all(abs(h.start * cpb - round(h.start * cpb)) < 1e-6 for h in hits):
            return cpb
    return 24


def render_tab(plan: SongPlan, hits: list[Hit], bars_per_line: int = 2, first_bar: int = 1,
               last_bar: int | None = None) -> str:
    last_bar = last_bar or plan.num_bars
    bpb = plan.beats_per_bar
    cpb = _cells_per_beat(hits)
    width = 3
    cells_per_bar = bpb * cpb

    grid: dict[tuple[int, int], dict[int, str]] = defaultdict(dict)  # (bar, cell) -> string -> text
    strum_mark: dict[tuple[int, int], str] = {}
    for h in hits:
        bar = int(h.start // bpb) + 1
        if not first_bar <= bar <= last_bar:
            continue
        cell = round((h.start - (bar - 1) * bpb) * cpb)
        if h.technique == "dead":
            text = "x"
        elif h.technique == "harmonic":
            text = f"<{h.fret}>"
        else:
            text = f"{h.fret}{SUFFIX.get(h.technique, '')}"
        grid[(bar, cell)][h.string] = text
        if h.strum_id is not None:
            strum_mark[(bar, cell)] = "^" if h.direction == "up" else "v"

    lines = [f"{plan.title} - {plan.tempo:g} bpm, {plan.time_signature[0]}/{plan.time_signature[1]}, "
             f"key {plan.key}   (v/^ = down/up strum, m = palm mute, x = dead, h/p/b/~ / = techniques)"]
    for start in range(first_bar, last_bar + 1, bars_per_line):
        bars = range(start, min(start + bars_per_line, last_bar + 1))
        chord_row, strum_row = "   ", "   "
        rows = {s: f"{'eBGDAE'[s - 1]} |" for s in range(1, 7)}
        for bar in bars:
            chord_cells = [" " * width] * cells_per_bar
            for i, sym in enumerate(plan.bar_chords(bar)):
                pos = int(i * cells_per_bar / len(plan.bar_chords(bar)))
                chord_cells[pos] = sym.ljust(width)
            chord_row += _join_labels(chord_cells, width) + " "
            for cell in range(cells_per_bar):
                strum_row += strum_mark.get((bar, cell), " ").ljust(width)
                for s in range(1, 7):
                    rows[s] += grid[(bar, cell)].get(s, "-").ljust(width, "-")
            strum_row += " "
            for s in range(1, 7):
                rows[s] += "|"
        lines.append("")
        lines.append(f"bar {start}")
        lines.append(chord_row.rstrip())
        lines.append(strum_row.rstrip())
        lines.extend(rows[s] for s in range(1, 7))
    return "\n".join(lines)


def _join_labels(cells: list[str], width: int) -> str:
    """Let chord names longer than a cell spill into the following empty cells."""
    text, carry = "", ""
    for c in cells:
        piece = carry + c.strip() if c.strip() else carry
        carry = piece[width:]
        text += piece[:width].ljust(width)
    return text
