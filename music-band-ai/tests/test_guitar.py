import json
from pathlib import Path
from types import SimpleNamespace

import mido
import pytest

from guitar.agent import GuitaristAgent, tool_chord_voicings, tool_scale_positions
from guitar.fretboard import fingers_needed, find_voicings, shape_problems
from guitar.part import GuitarPart, SongPlan
from guitar.render import RenderOptions, render_midi
from guitar.styles import STYLES, generate
from guitar.tab import render_tab
from guitar.theory import parse_chord
from guitar.validate import validate

PLANS = Path(__file__).parent.parent / "plans"


def small_plan(**kw) -> SongPlan:
    d = {"title": "t", "tempo": 100, "time_signature": [4, 4], "key": "G major",
         "chords": ["G", "C", "D", "G"]}
    d.update(kw)
    return SongPlan.from_dict(d)


# ------------------------------------------------------------------ theory / fretboard

@pytest.mark.parametrize("symbol,best", [
    ("C", "x32010"), ("G", "320003"), ("D", "xx0232"), ("A", "x02220"), ("E", "022100"),
    ("Am", "x02210"), ("Em", "022000"), ("Dm", "xx0231"), ("F", "133211"), ("B7", "x21202"),
])
def test_open_chords_come_first(symbol, best):
    assert find_voicings(parse_chord(symbol))[0].name == best


def test_voicings_respect_bass_and_essential_tones():
    chord = parse_chord("G/B")
    for v in find_voicings(chord, count=8):
        assert v.pitches[0] % 12 == 11
        assert chord.essential <= {p % 12 for p in v.pitches}


def test_power_chords_stay_small():
    for v in find_voicings(parse_chord("E5"), count=5):
        assert len(v.pitches) <= 3


def test_barre_counts_as_one_finger():
    assert fingers_needed([1, 3, 3, 2, 1, 1]) == (4, True)
    assert fingers_needed([None, 3, 2, 0, 1, 0]) == (3, False)
    assert shape_problems([1, 3, 3, 2, 1, 1]) == []
    assert shape_problems([1, 8, None, None, None, None])  # 8-fret stretch
    assert shape_problems([None, 3, 2, 0, 1])  # wrong length


def test_chord_parsing():
    assert parse_chord("F#m7").tone_names() == ["F#", "A", "C#", "E"]
    assert parse_chord("Bbmaj7").root == 10
    with pytest.raises(ValueError):
        parse_chord("Hmaj")


# ------------------------------------------------------------------ validation

def test_strum_pattern_follows_chord_changes():
    plan = small_plan(chords=["G C", "D"])
    part = {"tone": "acoustic_steel", "events": [{
        "type": "strum_pattern", "bars": [1, 2], "subdivision": 2, "pattern": "D.DU.UDU",
        "shapes": {"G": [3, 2, 0, 0, 0, 3], "C": [None, 3, 2, 0, 1, 0], "D": [None, None, 0, 2, 3, 2]}}]}
    v = validate(part, plan)
    assert v.ok, v.report()
    # C takes over at beat 3: the down-strum on beat 4 plays the A string at fret 3 (C), not 2 (B)
    beat4 = [h for h in v.hits if h.start == 3 and h.string == 5]
    assert beat4 and beat4[0].fret == 3
    beat1 = [h for h in v.hits if h.start == 0 and h.string == 5]
    assert beat1[0].fret == 2


def test_missing_shape_is_an_error_that_names_the_chord():
    plan = small_plan()
    part = {"tone": "acoustic_steel", "events": [{
        "type": "strum_pattern", "bars": [1, 4], "pattern": "D.DU.UDU",
        "shapes": {"G": [3, 2, 0, 0, 0, 3]}}]}
    v = validate(part, plan)
    assert not v.ok
    assert "C, D" in v.errors[0] and "get_chord_voicings" in v.errors[0]


def test_pattern_length_must_fit_the_bar():
    plan = small_plan()
    part = {"tone": "acoustic_steel", "events": [{
        "type": "strum_pattern", "bars": [1, 1], "pattern": "D.DU.UD",
        "shapes": {"G": [3, 2, 0, 0, 0, 3]}}]}
    assert "multiple of 8" in validate(part, plan).errors[0]


def test_impossible_double_stop_and_string_clash():
    plan = small_plan()
    part = {"tone": "electric_clean", "events": [
        {"type": "note", "bar": 1, "beat": 1, "string": 6, "fret": 3},
        {"type": "note", "bar": 1, "beat": 1, "string": 1, "fret": 12},
        {"type": "note", "bar": 2, "beat": 1, "string": 3, "fret": 5},
        {"type": "note", "bar": 2, "beat": 1, "string": 3, "fret": 7},
    ]}
    errors = " ".join(validate(part, plan).errors)
    assert "wider than" in errors
    assert "string 3" in errors


def test_pick_pattern_on_muted_string_is_rejected():
    plan = small_plan(chords=["D"])
    part = {"tone": "acoustic_nylon", "events": [{
        "type": "pick_pattern", "bars": [1, 1], "pattern": "B 5 2 1 B 3 2 1",
        "shapes": {"D": [None, None, 0, 2, 3, 2]}}]}
    assert "mutes it" in validate(part, plan).errors[0]


def test_out_of_key_long_note_warns():
    plan = small_plan()
    part = {"tone": "electric_clean", "events": [
        {"type": "note", "bar": 1, "beat": 1, "string": 2, "fret": 2, "duration": 2}]}  # C#
    v = validate(part, plan)
    assert v.ok and any("outside" in w for w in v.warnings)


@pytest.mark.parametrize("plan_file", sorted(PLANS.glob("*.json")))
@pytest.mark.parametrize("style", sorted(STYLES))
def test_every_offline_style_is_valid(plan_file, style):
    plan = SongPlan.load(plan_file)
    v = validate(generate(plan, style), plan)
    assert v.ok, v.report()


# ------------------------------------------------------------------ rendering

def test_down_strum_rolls_low_to_high_and_up_strum_high_to_low():
    plan = small_plan(chords=["G"])
    part = GuitarPart.from_dict({"tone": "acoustic_steel", "events": [
        {"type": "strum", "bar": 1, "beat": 1, "frets": [3, 2, 0, 0, 0, 3], "direction": "down"},
        {"type": "strum", "bar": 1, "beat": 3, "frets": [3, 2, 0, 0, 0, 3], "direction": "up"}]})
    v = validate(part, plan)
    mid = render_midi(plan, part, v.hits, options=RenderOptions(humanize=False))
    t, ons = 0, []
    for msg in mid.tracks[0]:
        t += msg.time
        if msg.type == "note_on":
            ons.append((t, msg.note))
    down = [n for t, n in ons if t < 480]
    up = [n for t, n in ons if t >= 960]
    assert down == sorted(down)
    assert up == sorted(up, reverse=True) and len(up) == 4  # up-strums catch the top strings
    assert ons[1][0] > ons[0][0]  # strings are spread in time


def test_one_note_per_string_and_bends_use_their_own_channel(tmp_path):
    plan = small_plan()
    part_d = {"tone": "overdrive", "events": [
        {"type": "note", "bar": 1, "beat": 1, "string": 3, "fret": 7, "duration": 4},
        {"type": "note", "bar": 1, "beat": 2, "string": 3, "fret": 9, "duration": 1,
         "technique": "bend", "bend": 2},
        {"type": "note", "bar": 2, "beat": 1, "string": 2, "fret": 8, "duration": 1,
         "technique": "slide", "slide_from": 5}]}
    part = GuitarPart.from_dict(part_d)
    v = validate(part, plan)
    assert v.ok, v.report()
    path = tmp_path / "g.mid"
    render_midi(plan, part, v.hits, path, RenderOptions(humanize=False))
    mid = mido.MidiFile(path)
    t, events = 0, []
    for msg in mid.tracks[0]:
        t += msg.time
        events.append((t, msg))
    off_62 = next(t for t, m in events if m.type == "note_off" and m.note == 62)
    on_64 = next(t for t, m in events if m.type == "note_on" and m.note == 64)
    assert off_62 <= on_64  # the held note stops when the same string is picked again
    assert any(m.type == "pitchwheel" and m.channel == 1 and m.pitch > 8000 for _, m in events)
    slide_notes = [m.note for _, m in events if m.type == "note_on" and m.channel == 0 and 64 <= m.note <= 67]
    assert slide_notes[-4:] == [64, 65, 66, 67]


def test_tab_shows_frets_and_strum_directions():
    plan = small_plan(chords=["C"])
    v = validate({"tone": "acoustic_steel", "events": [{
        "type": "strum_pattern", "bars": [1, 1], "pattern": "D.DU.UDU",
        "shapes": {"C": [None, 3, 2, 0, 1, 0]}}]}, plan)
    tab = render_tab(plan, v.hits)
    assert "A |3" in tab and "^" in tab and "v" in tab


# ------------------------------------------------------------------ agent loop (fake client)

def _block(**kw):
    return SimpleNamespace(**kw)


class FakeStream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


class FakeClient:
    """Plays back scripted assistant turns and records every request."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kw):
        self.requests.append({**kw, "messages": list(kw["messages"])})  # snapshot history
        content, stop = self.turns.pop(0)
        usage = SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0)
        return FakeStream(SimpleNamespace(content=content, stop_reason=stop, usage=usage))


GOOD_PART = {"tone": "acoustic_steel", "events": [
    {"type": "strum_pattern", "bars": [1, 3], "pattern": "D.DU.UDU",
     "shapes": {"G": [3, 2, 0, 0, 0, 3], "C": [None, 3, 2, 0, 1, 0], "D": [None, None, 0, 2, 3, 2]}},
    {"type": "strum", "bar": 4, "beat": 1, "frets": [3, 2, 0, 0, 0, 3], "duration": 4}]}


def test_agent_fixes_errors_then_finishes():
    bad = {"tone": "acoustic_steel", "events": [
        {"type": "strum_pattern", "bars": [1, 4], "pattern": "D.DU.UDU",
         "shapes": {"G": [3, 2, 0, 0, 0, 3]}}]}
    client = FakeClient([
        ([_block(type="thinking", thinking="plan: strum"),
          _block(type="tool_use", id="t1", name="get_chord_voicings", input={"chords": ["G", "C", "D"]})],
         "tool_use"),
        ([_block(type="tool_use", id="t2", name="submit_guitar_part", input=bad)], "tool_use"),
        ([_block(type="tool_use", id="t3", name="submit_guitar_part", input=GOOD_PART)], "tool_use"),
        ([_block(type="text", text="Strummed it, rang out the last G.")], "end_turn"),
    ])
    seen = []
    agent = GuitaristAgent(client=client, on_event=lambda k, t: seen.append(k))
    result = agent.compose(small_plan())

    assert result.part == GOOD_PART and result.validation.ok
    assert result.turns == 4 and result.summary.startswith("Strummed")
    second = client.requests[2]["messages"][-1]["content"][0]
    assert second["is_error"] and "C, D" in second["content"]
    third = client.requests[3]["messages"][-1]["content"][0]
    assert not third["is_error"] and third["content"].startswith("ACCEPTED")
    req = client.requests[0]
    assert req["model"] == "claude-opus-5-5"
    assert req["thinking"]["type"] == "adaptive" and req["fallbacks"] == "default"
    assert all(t["eager_input_streaming"] for t in req["tools"])
    assert {"thinking", "tool", "text"} <= set(seen)


def test_agent_nudges_once_when_it_forgets_to_submit():
    client = FakeClient([
        ([_block(type="text", text="I'd strum this.")], "end_turn"),
        ([_block(type="tool_use", id="t1", name="submit_guitar_part", input=GOOD_PART)], "tool_use"),
        ([_block(type="text", text="done")], "end_turn"),
    ])
    result = GuitaristAgent(client=client).compose(small_plan())
    assert result.validation.ok
    assert "submit_guitar_part" in client.requests[1]["messages"][-1]["content"]


def test_tool_helpers_text():
    text = tool_chord_voicings({"chords": ["C", "Hm"]})
    assert "[null, 3, 2, 0, 1, 0]" in text and "Hm:" in text
    assert "R" in tool_scale_positions({"scale": "E minor pentatonic", "position": 0})
    json.dumps(GOOD_PART)
