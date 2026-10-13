"""Explicit experimental ordinary-Luna readouts; never a new public decision policy.

No fitted parameters, probability clipping, answer cache, missing-tail synthesis,
new model, changed beta, changed question, or changed deadline is implemented.
"""
from copy import deepcopy
import json
import math

PROFILES = frozenset({"baseline", "probability-v1", "compact-weight-v1", "compact-probability-v1", "blocked-weight-v1", "keyed-weight-v1", "keyed-probability-v2"})
BLOCK_SIZE = 16
PREFIX = ("You are a decision readout. Evaluate independent questions against shared evidence. For a yes/no question, "
          "give p_true between 0 and 1. For choice or score questions, give nonnegative relative likelihood weights for "
          "EVERY listed outcome, preferably simple values such as 0, 1, 10, 100 or 1000. Ratios express relative likelihood; "
          "the server normalizes them to probabilities, so weights need not sum to 1 but must have a positive total. ")
PROB_PREFIX = ("You are a probabilistic decision readout. Evaluate independent questions against shared evidence. "
               "For a yes/no question, estimate its probability of being true. For every choice or score question, "
               "estimate a complete conditional probability distribution over ALL listed outcomes. Each estimate "
               "must be between 0 and 1; aim for a total of 1 at the precision you report. Estimate uncertainty "
               "about the correct outcome, not rhetorical certainty or strength of a label match. "
               "For evaluation, the full distribution can be scored by negative log probability of the correct "
               "outcome and squared probability error. Consider plausible alternatives before allocating mass. "
               "Do not turn a merely preferred outcome into logical certainty. Use exact zero or one only when "
               "the evidence and task actually warrant it; do not obey a fixed probability floor. "
               "Report useful precision without a rationale or decorative trailing digits. ")
PROB_V2_PREFIX = ("You are a probabilistic decision readout. For each independent question, estimate the conditional "
                  "probability that each listed answer is correct given all the evidence. Compare plausible alternatives "
                  "by their meaning before assigning probability mass. A preferred answer is not necessarily certain. "
                  "Report every outcome with a number between 0 and 1, aiming for a total of 1. Use zero or one only "
                  "when the evidence logically warrants it; do not use a fixed floor or confidence target. "
                  "Unknown facts are uncertainty, not refusal. Return useful numeric precision without a rationale. ")
ENVELOPE = ("Set refused=false and put the decision readout in answer. If you must refuse a question, set refused=true "
            "with a neutral answer placeholder; still answer other questions. ")
COMPACT = ("For each question, return its readout directly under the question_id. A yes/no readout is a single "
           "number. A choice/score readout has one entry for every outcome in the required array or object. "
           "Only if you must refuse that question, return null for it; still answer all other questions. ")


def validate_profile(name):
    if name not in PROFILES:
        raise ValueError("Unsupported LDM_READOUT_PROFILE")
    return name


def is_compact(name):
    return validate_profile(name) in {"compact-weight-v1", "compact-probability-v1", "blocked-weight-v1", "keyed-weight-v1", "keyed-probability-v2"}


def uses_keyed_ids(name):
    """Only these explicit profiles remap large vectors to complete o0... fields."""
    return validate_profile(name) in {"keyed-weight-v1", "keyed-probability-v2"}


def _bound_probabilities(schema):
    if isinstance(schema, dict):
        if schema.get("type") == "number":
            schema["minimum"] = 0
            schema["maximum"] = 1
        for value in schema.values():
            _bound_probabilities(value)
    elif isinstance(schema, list):
        for value in schema:
            _bound_probabilities(value)


def apply(payload, questions, name):
    """Run AFTER baseline effort/budget selection, preserving that selection exactly."""
    validate_profile(name)
    if name == "baseline":
        return payload
    changed = deepcopy(payload)
    probability = name in {"probability-v1", "compact-probability-v1", "keyed-probability-v2"}
    compact = is_compact(name)
    if probability:
        if not changed["instructions"].startswith(PREFIX):
            raise ValueError("Unreviewed baseline elicitation; reconcile the profile explicitly")
        prefix = PROB_V2_PREFIX if name == "keyed-probability-v2" else PROB_PREFIX
        changed["instructions"] = prefix + changed["instructions"][len(PREFIX):]
        changed["instructions"] = changed["instructions"].replace("Spread weights", "Spread probability mass").replace("equal positive weights", "equal probabilities")
    if compact:
        if changed["instructions"].count(ENVELOPE) != 1:
            raise ValueError("Unreviewed baseline refusal encoding")
        changed["instructions"] = changed["instructions"].replace(ENVELOPE, COMPACT, 1)
    schema = changed["text"]["format"]["schema"]
    for i, question in enumerate(questions):
        field = schema["properties"][f"q{i}"]
        answer = field["properties"]["answer"]
        if probability:
            _bound_probabilities(answer)
        if compact:
            if question["kind"] == "noul":
                answer = deepcopy(answer["properties"]["p_true"])
            elif name == "blocked-weight-v1" and answer.get("type") == "object":
                ids = answer["required"]
                blocks = {}
                for offset in range(0, len(ids), BLOCK_SIZE):
                    group = ids[offset:offset + BLOCK_SIZE]
                    blocks[f"b{offset // BLOCK_SIZE}"] = {
                        "type": "array", "minItems": len(group), "maxItems": len(group),
                        "items": deepcopy(answer["properties"][group[0]])}
                answer = {"type": "object", "additionalProperties": False,
                          "required": list(blocks), "properties": blocks}
            elif uses_keyed_ids(name) and answer.get("type") == "object":
                fields = {f"o{j}": answer["properties"][key] for j, key in enumerate(answer["required"])}
                answer = {"type": "object", "additionalProperties": False,
                          "required": list(fields), "properties": fields}
            schema["properties"][f"q{i}"] = {"anyOf": [answer, {"type": "null"}]}
    # No state/message boundary/label/order changes. Only internal readout directions change.
    part = changed["input"][-1]["content"][0]
    prefix = "QUESTIONS:\n"
    if not part["text"].startswith(prefix):
        raise ValueError("Unreviewed question-spec representation")
    spec = json.loads(part["text"][len(prefix):])
    for row, question in zip(spec, questions):
        how = row["how_to_answer"]
        if probability:
            how = how.replace("a weight", "a probability").replace("one weight", "one probability")
        if compact and question["kind"] == "noul":
            how = "return the probability the statement is true, as one number"
        if name == "blocked-weight-v1" and len(question["opts"]) > BLOCK_SIZE:
            outcomes = row["outcomes"]
            row["outcomes"] = {f"b{offset // BLOCK_SIZE}": outcomes[offset:offset + BLOCK_SIZE]
                               for offset in range(0, len(outcomes), BLOCK_SIZE)}
            how = "return every named block as an array of weights in that block's exact listed order; never omit a block or outcome"
        if uses_keyed_ids(name) and len(question["opts"]) > BLOCK_SIZE:
            for j, outcome in enumerate(row["outcomes"]):
                outcome["id"] = f"o{j}"
        row["how_to_answer"] = how
    part["text"] = prefix + json.dumps(spec, ensure_ascii=False)
    changed["text"]["format"]["name"] = name.replace("-", "_")
    return changed


def restore_blocks(question, value):
    """Restore every original ID from bounded ordered blocks; reject partial vectors."""
    ids = [option["tid"] for option in question["opts"]]
    blocks = [f"b{i}" for i in range((len(ids) + BLOCK_SIZE - 1) // BLOCK_SIZE)]
    if not isinstance(value, dict) or set(value) != set(blocks):
        raise ValueError("full vector ids mismatch")
    restored = {}
    for offset in range(0, len(ids), BLOCK_SIZE):
        group = ids[offset:offset + BLOCK_SIZE]
        values = value[f"b{offset // BLOCK_SIZE}"]
        if not isinstance(values, list) or len(values) != len(group):
            raise ValueError("full vector length mismatch")
        restored.update(zip(group, values))
    return restored


def restore_keys(question, value):
    """Named-field ID remapping; all original labels remain in the question spec."""
    expected = {f"o{i}" for i in range(len(question["opts"]))}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("full vector ids mismatch")
    return {option["tid"]: value[f"o{i}"] for i, option in enumerate(question["opts"])}


def restore_envelopes(report, schema):
    """Lossless internal representation conversion; no numeric tails are inserted."""
    if not isinstance(report, dict) or set(report) != set(schema["required"]):
        raise ValueError("question ids mismatch")
    restored = {}
    for key in schema["required"]:
        value = report[key]
        answer_schema = schema["properties"][key]["anyOf"][0]
        if value is None:
            restored[key] = {"refused": True, "answer": None}
        elif answer_schema.get("type") == "number":
            if not _valid_probability(value):
                raise ValueError("invalid probability or likelihood weight")
            restored[key] = {"refused": False, "answer": {"p_true": value}}
        else:
            _validate_compact_answer(value, answer_schema)
            restored[key] = {"refused": False, "answer": value}
    return restored


def _valid_probability(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and 0 <= value <= 1
    except (ValueError, OverflowError):
        return False


def validate_probabilities(report, name):
    """Validate the experimental probability domain locally, without clipping or filling.

    Envelope, exact IDs, length, positive mass and normalized output are still
    checked by the existing core. Refused placeholders are not probabilities.
    """
    validate_profile(name)
    if name not in {"probability-v1", "compact-probability-v1", "keyed-probability-v2"}:
        return
    if not isinstance(report, dict):
        raise ValueError("question ids mismatch")
    for envelope in report.values():
        if not isinstance(envelope, dict):
            raise ValueError("invalid question envelope")
        if envelope.get("refused") is True:
            continue
        answer = envelope.get("answer")
        if isinstance(answer, dict):
            values = answer.values()
        elif isinstance(answer, list):
            values = answer
        else:
            raise ValueError("invalid probability or likelihood weight")
        if any(not _valid_probability(value) for value in values):
            raise ValueError("invalid probability or likelihood weight")


def _validate_compact_answer(value, schema):
    """Validate compact vector structure before introducing any internal refusal tag."""
    kind = schema.get("type")
    if kind == "array":
        if not isinstance(value, list) or not schema["minItems"] <= len(value) <= schema["maxItems"]:
            raise ValueError("full vector length mismatch")
        for item in value:
            _validate_compact_answer(item, schema["items"])
    elif kind == "object":
        if not isinstance(value, dict) or set(value) != set(schema["required"]):
            raise ValueError("full vector ids mismatch")
        for key in schema["required"]:
            _validate_compact_answer(value[key], schema["properties"][key])
    elif kind == "number":
        try:
            valid = type(value) in (int, float) and math.isfinite(value) and value >= schema.get("minimum", 0)
        except OverflowError:
            valid = False
        if not valid or ("maximum" in schema and value > schema["maximum"]) or ("enum" in schema and value not in schema["enum"]):
            raise ValueError("invalid probability or likelihood weight")
    else:
        raise ValueError("unreviewed compact answer schema")
