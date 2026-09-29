"""The guitarist agent: Claude writes the part, local tools keep it playable.

Loop: Claude reads the song plan -> asks for chord voicings / scale positions -> submits a
part -> the validator answers with errors (fix and resubmit) or an accepted tab preview.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from .fretboard import find_voicings, scale_map, tuning_of
from .part import GuitarPart, SongPlan
from .prompts import SYSTEM_PROMPT, TOOLS
from .tab import render_tab
from .theory import parse_chord, parse_scale
from .validate import Validation, validate

DEFAULT_MODEL = "claude-opus-5-5"
# models that take adaptive thinking + effort, and the server-side refusal fallback
ADAPTIVE_MODELS = ("claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1")
FALLBACK_BETA = "server-side-fallback-2026-07-01"

EventFn = Callable[[str, str], None]


class AgentError(RuntimeError):
    pass


@dataclass
class AgentResult:
    part: dict
    validation: Validation
    summary: str
    turns: int
    usage: dict = field(default_factory=dict)


def describe_plan(plan: SongPlan) -> str:
    """The plan as JSON plus a bar map the model can scan quickly."""
    lines = [json.dumps(plan.to_dict(), indent=2), "", "Bar map:"]
    runs = plan.sections or [{"name": "song", "start_bar": 1, "bars": plan.num_bars}]
    for s in runs:
        first = s["start_bar"]
        last = min(first + s["bars"] - 1, plan.num_bars)
        energy = f", energy {s['energy']}" if "energy" in s else ""
        chords = " | ".join(plan.chords[first - 1:last])
        lines.append(f"  bars {first}-{last} {s['name']}{energy}: {chords}")
    distinct = []
    for c in plan.chords:
        for sym in c.split():
            if sym not in distinct:
                distinct.append(sym)
    lines += [
        "",
        f"{plan.num_bars} bars, {plan.beats_per_bar} beats per bar, feel: {plan.feel}.",
        f"Distinct chords: {', '.join(distinct)}",
    ]
    return "\n".join(lines)


class GuitaristAgent:
    def __init__(self, client: Any = None, model: str = DEFAULT_MODEL, effort: str = "high",
                 max_turns: int = 12, on_event: EventFn | None = None):
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.effort = effort
        self.max_turns = max_turns
        self.on_event = on_event or (lambda kind, text: None)

    # ------------------------------------------------------------------ loop

    def compose(self, plan: SongPlan, brief: str = "") -> AgentResult:
        brief = brief or plan.guitar_brief
        user = (f"Here is the song plan.\n\n{describe_plan(plan)}\n\n"
                f"Brief from the conductor: {brief or 'Play what serves the song.'}\n\n"
                "Write the guitar part.")
        messages: list[dict] = [{"role": "user", "content": user}]
        accepted: tuple[dict, Validation] | None = None
        summary = ""
        usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}
        nudged = False

        for turn in range(1, self.max_turns + 1):
            response = self._call(messages)
            for k in usage:
                usage[k] += getattr(response.usage, k, 0) or 0
            # append the whole content (thinking blocks included) - history stays append-only
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "refusal":
                raise AgentError("the model declined this request")
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason == "max_tokens" and tool_uses:
                raise AgentError("ran out of output tokens mid tool call; the part is too long - "
                                 "try fewer bars or more patterns")

            for block in response.content:
                if block.type == "thinking" and getattr(block, "thinking", ""):
                    self.on_event("thinking", block.thinking)
                elif block.type == "text" and block.text.strip():
                    self.on_event("text", block.text)
                    summary = block.text.strip()

            if not tool_uses:
                if accepted:
                    return AgentResult(accepted[0], accepted[1], summary, turn, usage)
                if nudged or response.stop_reason not in ("end_turn", "pause_turn"):
                    raise AgentError("the agent stopped without an accepted part")
                nudged = True
                messages.append({"role": "user", "content":
                                 "No accepted part yet. Write the part and call submit_guitar_part."})
                continue

            results = []
            for block in tool_uses:
                content, is_error, validation = self._run_tool(block.name, block.input, plan)
                if validation is not None and validation.ok:
                    accepted = (dict(block.input), validation)
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": content, "is_error": is_error})
            messages.append({"role": "user", "content": results})

        if accepted:
            return AgentResult(accepted[0], accepted[1], summary, self.max_turns, usage)
        raise AgentError(f"no accepted part after {self.max_turns} turns")

    def _request(self) -> dict:
        tools = [{**t, "eager_input_streaming": True} for t in TOOLS]
        kw: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 64000,
            "system": SYSTEM_PROMPT,
            "tools": tools,
            "cache_control": {"type": "ephemeral"},
        }
        if self.model in ADAPTIVE_MODELS:
            kw["thinking"] = {"type": "adaptive", "display": "summarized"}
            kw["output_config"] = {"effort": self.effort}
            kw["betas"] = [FALLBACK_BETA]
            kw["fallbacks"] = "default"
        return kw

    def _call(self, messages: list[dict]):
        kw = self._request()
        for attempt in range(3):
            try:
                with self.client.beta.messages.stream(messages=messages, **kw) as stream:
                    return stream.get_final_message()
            except ValueError:
                # a streamed tool input that isn't parseable JSON; ask again
                if attempt == 2:
                    raise
        raise AssertionError("unreachable")

    # ------------------------------------------------------------------ tools

    def _run_tool(self, name: str, args: Any, plan: SongPlan) -> tuple[str, bool, Validation | None]:
        if not isinstance(args, dict):
            return "INVALID_INPUT: tool input must be a JSON object", True, None
        try:
            if name == "get_chord_voicings":
                self.on_event("tool", f"get_chord_voicings {args.get('chords')}")
                return tool_chord_voicings(args), False, None
            if name == "get_scale_positions":
                self.on_event("tool", f"get_scale_positions {args.get('scale')} @ {args.get('position')}")
                return tool_scale_positions(args), False, None
            if name == "submit_guitar_part":
                v = validate(args, plan)
                self.on_event("tool", f"submit_guitar_part ({len(args.get('events') or [])} events): "
                                      + ("accepted" if v.ok else f"{len(v.errors)} errors"))
                if not v.ok:
                    return v.report(), True, v
                preview = render_tab(plan, v.hits, first_bar=1, last_bar=min(4, plan.num_bars))
                return (f"ACCEPTED.\n{v.report()}\n\nTab preview (bars 1-4):\n{preview}"), False, v
        except (ValueError, TypeError, KeyError) as e:
            return f"INVALID_INPUT: {e}", True, None
        return f"unknown tool {name!r}", True, None


def tool_chord_voicings(args: dict) -> str:
    chords = args.get("chords")
    if not isinstance(chords, list) or not all(isinstance(c, str) for c in chords):
        raise ValueError("'chords' must be a list of chord symbols")
    tuning = tuning_of(args.get("tuning", "standard"))
    count = max(1, min(int(args.get("count", 4)), 8))
    strings = args.get("strings")
    search = {
        "near_fret": args.get("near_fret"),
        "min_strings": int(args.get("min_strings", 4)),
        "max_strings": int(args.get("max_strings", 6)),
        "allowed_strings": set(strings) if strings else None,
    }
    out = []
    for sym in chords:
        try:
            chord = parse_chord(sym)
        except ValueError as e:
            out.append(f"{sym}: {e}")
            continue
        found = find_voicings(chord, tuning, count=count, **search)
        out.append(f"{sym} ({' '.join(chord.tone_names())}):")
        if not found:
            out.append("  no voicing fits these limits - relax strings/min_strings/near_fret")
        for v in found:
            out.append(f"  {json.dumps(list(v.frets))}  {v.describe()}")
    return "\n".join(out)


def tool_scale_positions(args: dict) -> str:
    scale = parse_scale(str(args.get("scale", "")))
    position = int(args.get("position", 0))
    if not 0 <= position <= 17:
        raise ValueError("position must be 0-17")
    return scale_map(scale, tuning_of(args.get("tuning", "standard")), position)
