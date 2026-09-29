"""Hits -> a Standard MIDI File that sounds like a guitar rather than a keyboard.

What makes guitar MIDI believable, and what this module does about it:
- Strums: strings sound one after another (low->high on down-strums, high->low on up),
  a few milliseconds apart, faster when played harder.
- One note per string: a new note on a string cuts the previous one (no piano-style
  overlaps), while other strings keep ringing.
- Techniques: palm mutes are short and soft, dead strums are clicks, slides are a quick
  chromatic run into the target, bends and vibrato use pitch-wheel on their own channel
  so they don't bend the rest of the chord.
- Feel: optional swing, and small seeded timing/velocity variation.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from pathlib import Path

import mido

from .fretboard import tuning_of
from .part import TONES, GuitarPart, Hit, SongPlan

PPQ = 480
MAIN_CH = 0
BEND_CH = 1  # bends/vibrato get their own channel so the pitch wheel can't bend a chord
BEND_RANGE = 2  # semitones, the General MIDI default (set explicitly via RPN too)


@dataclass
class RenderOptions:
    humanize: bool = True
    seed: int = 7
    swing: float | None = None  # 0..1, None = from plan.feel ("swing" -> 1.0 = triplet swing)
    strum_ms: float | None = None  # per-string strum delay; None = derived from velocity


@dataclass
class _Note:
    on: float  # seconds
    off: float
    pitch: int
    velocity: int
    channel: int
    string: int
    hit: Hit


def _swing_warp(beat_pos: float, amount: float) -> float:
    """Delay off-beat eighths. amount=1 puts the 'and' on the last triplet (2:1 swing)."""
    if amount <= 0:
        return beat_pos
    whole = math.floor(beat_pos)
    frac = beat_pos - whole
    mid = 0.5 + amount / 6
    frac = frac / 0.5 * mid if frac <= 0.5 else mid + (frac - 0.5) / 0.5 * (1 - mid)
    return whole + frac


def render_midi(plan: SongPlan, part: GuitarPart, hits: list[Hit], path: str | Path | None = None,
                options: RenderOptions | None = None) -> mido.MidiFile:
    opts = options or RenderOptions()
    rng = random.Random(opts.seed)
    tuning = tuning_of(part.tuning)
    sec_per_quarter = 60.0 / plan.tempo
    sec_per_beat = sec_per_quarter * plan.quarters_per_beat
    swing = opts.swing if opts.swing is not None else (1.0 if plan.feel == "swing" else 0.0)
    # swing is defined on quarter-note beats; in x/8 time leave it straight
    if plan.time_signature[1] != 4:
        swing = 0.0

    def to_sec(beats: float) -> float:
        return _swing_warp(beats, swing) * sec_per_beat

    strum_jitter: dict[int, float] = {}
    notes: list[_Note] = []
    extra: list[tuple[float, mido.Message]] = []  # pitch wheel events

    for h in hits:
        on = to_sec(h.start)
        off = to_sec(h.start + h.duration)
        vel = h.velocity
        if h.strum_id is not None:
            # whole strum drifts together, then strings fan out in pick order
            if h.strum_id not in strum_jitter:
                strum_jitter[h.strum_id] = rng.gauss(0, 0.006) if opts.humanize else 0.0
            per_string = (opts.strum_ms if opts.strum_ms is not None
                          else 6 + (127 - vel) / 127 * 10) / 1000
            if h.technique == "dead":
                per_string *= 0.5
            on += strum_jitter[h.strum_id] + h.strum_index * per_string
        elif opts.humanize:
            on += max(-0.012, min(0.012, rng.gauss(0, 0.005)))
        if opts.humanize:
            vel += rng.randint(-5, 5)

        channel = MAIN_CH
        if h.technique == "palm_mute":
            off = min(off, on + min(0.18, 0.6 * sec_per_beat))
            vel -= 12
        elif h.technique == "dead":
            off = on + 0.035
            vel = min(vel, 70) - 10
        elif h.technique in ("hammer_on", "pull_off"):
            vel = int(vel * 0.75)
        elif h.technique == "harmonic":
            vel = int(vel * 0.85)
        elif h.technique in ("bend", "vibrato"):
            channel = BEND_CH

        pitch = h.pitch(tuning)
        if h.technique == "slide" and h.slide_from is not None:
            step = 1 if h.fret > h.slide_from else -1
            run = list(range(h.slide_from, h.fret, step))
            step_sec = min(0.03, 0.25 * sec_per_beat / max(len(run), 1))
            t0 = on - step_sec * len(run)
            base = pitch - h.fret
            for k, fret in enumerate(run):
                t = max(0.0, t0 + k * step_sec)
                notes.append(_Note(t, t + step_sec, base + fret, max(20, int(vel * (0.6 if k else 0.8))),
                                   channel, h.string, h))
            vel = int(vel * 0.8)

        if h.technique == "bend":
            rise = min(0.35 * (off - on), 0.6 * sec_per_beat, 0.25)
            steps = 10
            for k in range(1, steps + 1):
                amount = h.bend * k / steps
                extra.append((on + rise * k / steps, _wheel(amount)))
            extra.append((off + 0.001, _wheel(0)))
        elif h.technique == "vibrato":
            start = on + 0.3 * (off - on)
            t = start
            while t < off - 0.02:
                amount = 0.3 * math.sin(2 * math.pi * 5.5 * (t - start))
                extra.append((t, _wheel(amount)))
                t += 0.025
            extra.append((off + 0.001, _wheel(0)))

        notes.append(_Note(max(0.0, on), off, pitch, max(1, min(127, vel)), channel, h.string, h))

    _one_note_per_string(notes)
    _no_same_pitch_overlap(notes)

    mid = mido.MidiFile(ticks_per_beat=PPQ)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    program = TONES[part.tone]
    header = [
        mido.MetaMessage("track_name", name=f"Guitar ({part.tone})", time=0),
        mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(plan.tempo), time=0),
        mido.MetaMessage("time_signature", numerator=plan.time_signature[0],
                         denominator=plan.time_signature[1], time=0),
        mido.Message("program_change", channel=MAIN_CH, program=program, time=0),
        mido.Message("program_change", channel=BEND_CH, program=program, time=0),
    ]
    # pitch-bend range via RPN 0 on the bend channel
    for cc, val in ((101, 0), (100, 0), (6, BEND_RANGE), (38, 0), (101, 127), (100, 127)):
        header.append(mido.Message("control_change", channel=BEND_CH, control=cc, value=val, time=0))
    track.extend(header)

    def tick(sec: float) -> int:
        return max(0, round(sec / sec_per_quarter * PPQ))

    events: list[tuple[int, int, mido.Message]] = []
    for n in notes:
        on_t, off_t = tick(n.on), tick(n.off)
        if off_t <= on_t:
            off_t = on_t + 1
        events.append((on_t, 2, mido.Message("note_on", channel=n.channel, note=n.pitch,
                                             velocity=n.velocity)))
        events.append((off_t, 0, mido.Message("note_off", channel=n.channel, note=n.pitch,
                                              velocity=0)))
    for sec, msg in extra:
        events.append((tick(sec), 1, msg))
    events.sort(key=lambda e: (e[0], e[1]))

    now = 0
    for t, _, msg in events:
        track.append(msg.copy(time=t - now))
        now = t
    end = tick(plan.total_beats * sec_per_beat)
    track.append(mido.MetaMessage("end_of_track", time=max(0, end - now)))

    if path is not None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        mid.save(str(path))
    return mid


def _wheel(semitones: float) -> mido.Message:
    value = int(round(8191 * semitones / BEND_RANGE))
    return mido.Message("pitchwheel", channel=BEND_CH, pitch=max(-8192, min(8191, value)))


def _one_note_per_string(notes: list[_Note]) -> None:
    """A string can only sound one note: each note stops when the next one on its string starts."""
    by_string: dict[int, list[_Note]] = {}
    for n in sorted(notes, key=lambda n: n.on):
        by_string.setdefault(n.string, []).append(n)
    for seq in by_string.values():
        for a, b in zip(seq, seq[1:]):
            if a.off > b.on - 0.005:
                a.off = max(a.on + 0.01, b.on - 0.005)


def _no_same_pitch_overlap(notes: list[_Note]) -> None:
    """Two strings can hit the same pitch; MIDI can't hold one pitch twice on a channel."""
    by_key: dict[tuple[int, int], list[_Note]] = {}
    for n in sorted(notes, key=lambda n: n.on):
        by_key.setdefault((n.channel, n.pitch), []).append(n)
    for seq in by_key.values():
        for a, b in zip(seq, seq[1:]):
            if a.off > b.on - 0.002:
                a.off = max(a.on + 0.005, b.on - 0.002)
