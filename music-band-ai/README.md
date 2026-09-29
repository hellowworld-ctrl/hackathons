# Music Band AI: the guitarist

The first musician in a multi-agent band. It takes a **song plan** (tempo, meter, key,
sections, chords per bar) and writes a **guitar part a real player could play**, then exports
it as MIDI plus ASCII tab.

```
song plan (JSON) ──► Guitarist agent (Claude) ──► guitar part (JSON) ──► validator ──► MIDI + tab
                       │   ▲                                               │
                       │   └──────── errors / warnings / tab preview ◄─────┘
                       ▼
             get_chord_voicings / get_scale_positions  (fretboard search, local)
```

**Split of work:** Claude makes the musical decisions: role, groove, dynamics, voicing
choice, fills and licks. Local code handles the physics and the MIDI, which LLMs are bad at:
- **Fretboard search** finds real chord shapes: C → `x32010`, F → `133211` barre, power
  chords on the low strings, compact 4-string funk and jazz shapes near a chosen fret.
- **Validator** rejects what one hand can't do: a stretch wider than 5 frets, more than
  4 fingers (a barre counts as 1), two notes on one string, timing outside the song. It
  also flags musical smells: wrong-chord shapes, out-of-key notes on strong beats, clutter.
  Errors go back to the agent, which fixes them and resubmits.
- **Renderer** makes the MIDI sound like a guitar, not a keyboard:
  - strums roll across the strings a few ms apart (down = low→high, up = high→low, mostly the top strings)
  - one note per string (a new note cuts the old one while other strings keep ringing)
  - palm mutes and dead strums are short
  - slides are quick chromatic runs into the target note
  - bends and vibrato use the pitch wheel on their own channel
  - swing feel, and seeded humanized timing and velocity

## Quick start

```bash
cd music-band-ai
pip install -r requirements.txt

# no API key needed: rule-based parts
python -m guitar offline plans/pop_ballad.json --style travis --out out/ballad
python -m guitar voicings C G Am F --near-fret 5

# Claude writes the part (ANTHROPIC_API_KEY or `ant auth login`)
python -m guitar compose plans/rock_drive.json --out out/rock -v

# hand-edit a part JSON and re-render it
python -m guitar render plans/rock_drive.json examples/night_drive.handwritten.part.json --out out/nd

# also render audio (needs fluidsynth + a General MIDI .sf2, e.g. FluidR3_GM.sf2)
python -m guitar offline plans/pop_ballad.json --out out/ballad --soundfont /usr/share/sounds/sf2/FluidR3_GM.sf2
```

Each run writes `<out>.mid`, `<out>.part.json` and `<out>.tab.txt`.
Offline styles: `pop_strum`, `ballad_arpeggio`, `travis`, `rock_power`, `funk`, `jazz_comp`.

`compose` defaults to `claude-opus-5-5` with adaptive thinking (`-v` prints the
guitarist's reasoning) and effort `high`. It also opts into server-side refusal fallback
(`fallbacks: "default"`). Use `--model` / `--effort` to trade quality for speed and cost.

## The song plan (shared contract for every future instrument agent)

```json
{
  "title": "Rainy Window", "tempo": 84, "time_signature": [4, 4], "key": "G major",
  "feel": "straight",
  "sections": [{"name": "verse", "start_bar": 1, "bars": 8, "energy": 0.5}],
  "chords": ["G", "Em", "C D", "G"],
  "guitar_brief": "sparse intro, confident strummed chorus"
}
```

`chords` has one entry per bar; `"C D"` splits the bar evenly. `energy` (0 to 1) drives
dynamics. Tempo is in quarter notes per minute. See `plans/` for three examples.

## The guitar part format

Four event types. Patterns keep a part short and idiomatic; single events give full control.

| type | what it is | example |
|---|---|---|
| `strum_pattern` | strumming groove over a bar range | `"pattern": "D.DU.UDU"`, `"shapes": {"G": [3,2,0,0,0,3]}` |
| `pick_pattern` | fingerpicking over a bar range | `"pattern": "B 3 2 3 1 3 2 3"` (B = bass string) |
| `strum` | one strum | `{"bar": 4, "beat": 4.5, "frets": [null,3,2,0,1,0], "direction": "up"}` |
| `note` | one note, optional technique | `{"string": 3, "fret": 7, "technique": "bend", "bend": 2}` |

- A shape lists 6 frets from low E to high E (`null` = muted).
- Strings are numbered 6 (low E) to 1 (high E). Bars and beats are 1-based.
- Strum characters: `D`/`U` strum, `d`/`u` ghost strum, `X`/`x` dead scratch, `.` let it ring.
- Techniques: `palm_mute`, `hammer_on`, `pull_off`, `slide` (+`slide_from`),
  `bend` (+`bend`), `vibrato`, `harmonic`, `dead`.

The full spec the agent sees is in `guitar/prompts.py`. A hand-written part that exercises
the techniques is in `examples/night_drive.handwritten.part.json`.

## Layout

```
guitar/
  theory.py     notes, chord symbols, scales
  fretboard.py  tunings, playability rules, voicing search, scale maps
  part.py       song plan + part format, pattern expansion into hits
  validate.py   errors (unplayable) and warnings (musical smells)
  render.py     hits -> guitar-like MIDI
  tab.py        ASCII tab
  styles.py     rule-based parts (baseline / no-API mode)
  prompts.py    the guitarist's system prompt + tool schemas
  agent.py      Claude tool-use loop (stream, validate, retry)
plans/          example song plans
examples/       generated parts: .mid, .part.json, .tab.txt
tests/          pytest (agent loop tested with a scripted fake client)
```

`python -m pytest tests` runs the suite (no API calls).

## Next steps toward the band

1. **Listen and tune.** Render 10-20 parts per style, note what sounds wrong, then fix the
   prompt (guitar idioms) or the renderer (feel). This is where quality comes from.
2. **Give the agent ears.** Pass other agents' parts (at minimum the bass notes and kick
   drum rhythm) into `compose` so the guitar can lock with them.
3. **Reuse the pattern for the next instruments.** A bass agent is the same design with a
   different fretboard (4 strings) and prompt. Keys and drums need their own
   playability rules.
