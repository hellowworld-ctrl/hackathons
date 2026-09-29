"""Command line: python -m guitar <command> ...

  compose   Claude writes the part (needs ANTHROPIC_API_KEY or `ant auth login`)
  offline   rule-based part from a style, no API needed
  render    re-render an edited .part.json to MIDI + tab
  voicings  print chord shapes, e.g. python -m guitar voicings C G Am F
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from .fretboard import find_voicings, tuning_of
from .part import GuitarPart, PartError, SongPlan
from .render import RenderOptions, render_midi
from .styles import STYLES, generate
from .tab import render_tab
from .theory import parse_chord
from .validate import validate


def _with(out: Path, ext: str) -> Path:
    """out/pop.travis + .mid -> out/pop.travis.mid (with_suffix would eat '.travis')."""
    return out.parent / f"{out.name}{ext}"


def _write_outputs(plan: SongPlan, part_dict: dict, out: Path, args) -> int:
    v = validate(part_dict, plan)
    if not v.ok:
        print(v.report(), file=sys.stderr)
        return 1
    part = GuitarPart.from_dict(part_dict)
    out.parent.mkdir(parents=True, exist_ok=True)
    midi_path = _with(out, ".mid")
    render_midi(plan, part, v.hits, midi_path,
                RenderOptions(humanize=not args.no_humanize, seed=args.seed))
    _with(out, ".part.json").write_text(json.dumps(part_dict, indent=2) + "\n")
    tab = render_tab(plan, v.hits)
    _with(out, ".tab.txt").write_text(tab + "\n")
    if v.warnings:
        print(v.report())
    print(f"\nwrote {midi_path}, {_with(out, '.part.json')}, {_with(out, '.tab.txt')}")
    if args.soundfont:
        _render_wav(midi_path, Path(args.soundfont))
    return 0


def _render_wav(midi: Path, soundfont: Path) -> None:
    if not shutil.which("fluidsynth"):
        print("fluidsynth not found on PATH; skipping WAV (brew/apt install fluidsynth)")
        return
    wav = midi.parent / f"{midi.stem}.wav"
    subprocess.run(["fluidsynth", "-ni", "-g", "0.8", "-F", str(wav), "-r", "44100",
                    str(soundfont), str(midi)], check=True)
    print(f"wrote {wav}")


def cmd_compose(args) -> int:
    from .agent import AgentError, GuitaristAgent

    plan = SongPlan.load(args.plan)

    def show(kind: str, text: str) -> None:
        if kind == "thinking" and args.verbose:
            print(f"\n[guitarist thinking]\n{text}\n")
        elif kind == "tool":
            print(f"  -> {text}")
        elif kind == "text":
            print(f"\n[guitarist] {text}")

    agent = GuitaristAgent(model=args.model, effort=args.effort, on_event=show)
    print(f"Guitarist ({args.model}, effort {args.effort}) is reading '{plan.title}'...")
    try:
        result = agent.compose(plan, brief=args.brief or "")
    except AgentError as e:
        print(f"agent failed: {e}", file=sys.stderr)
        return 1
    u = result.usage
    print(f"\n{result.turns} turns, {u['input_tokens']} in / {u['output_tokens']} out tokens "
          f"({u['cache_read_input_tokens']} cached)")
    return _write_outputs(plan, result.part, Path(args.out), args)


def cmd_offline(args) -> int:
    plan = SongPlan.load(args.plan)
    return _write_outputs(plan, generate(plan, args.style), Path(args.out), args)


def cmd_render(args) -> int:
    plan = SongPlan.load(args.plan)
    return _write_outputs(plan, json.loads(Path(args.part).read_text()), Path(args.out), args)


def cmd_voicings(args) -> int:
    tuning = tuning_of(args.tuning)
    for sym in args.chords:
        chord = parse_chord(sym)
        print(f"{sym} ({' '.join(chord.tone_names())})")
        for v in find_voicings(chord, tuning, count=args.count, near_fret=args.near_fret):
            print(f"  {v.describe()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m guitar", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def outputs(sp):
        sp.add_argument("--out", required=True, help="output path stem, e.g. out/ballad")
        sp.add_argument("--seed", type=int, default=7, help="humanize seed")
        sp.add_argument("--no-humanize", action="store_true", help="exact grid timing")
        sp.add_argument("--soundfont", help=".sf2 file; also renders a WAV via fluidsynth")

    c = sub.add_parser("compose", help="Claude writes the part")
    c.add_argument("plan")
    c.add_argument("--brief", help="override the plan's guitar_brief")
    c.add_argument("--model", default="claude-opus-5-5")
    c.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    c.add_argument("-v", "--verbose", action="store_true", help="show the guitarist's thinking")
    outputs(c)
    c.set_defaults(fn=cmd_compose)

    o = sub.add_parser("offline", help="rule-based part, no API")
    o.add_argument("plan")
    o.add_argument("--style", default="pop_strum", choices=sorted(STYLES))
    outputs(o)
    o.set_defaults(fn=cmd_offline)

    r = sub.add_parser("render", help="validate + render an existing part JSON")
    r.add_argument("plan")
    r.add_argument("part")
    outputs(r)
    r.set_defaults(fn=cmd_render)

    v = sub.add_parser("voicings", help="print chord shapes")
    v.add_argument("chords", nargs="+")
    v.add_argument("--near-fret", type=int)
    v.add_argument("--count", type=int, default=5)
    v.add_argument("--tuning", default="standard")
    v.set_defaults(fn=cmd_voicings)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except PartError as e:
        print(f"invalid input:\n{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
