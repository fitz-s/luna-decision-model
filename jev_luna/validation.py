"""Strict bounded JSON without changing evidence integers or object ordering."""
import json
import math


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite JSON number")
    return number


def _int(value):
    if len(value.lstrip("-")) > 4300:
        raise ValueError("JSON integer exceeds the operational digit budget")
    return int(value)


def loads(data, *, max_depth=128, max_items=1_000_000):
    try:
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        value = json.loads(data, object_pairs_hook=_pairs, parse_float=_float, parse_int=_int,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON constant")))
        pending, count = [(value, 0)], 0
        while pending:
            item, depth = pending.pop()
            count += 1
            if depth > max_depth or count > max_items:
                raise ValueError("JSON exceeds the operational depth/item budget")
            if isinstance(item, str):
                item.encode("utf-8")
            elif isinstance(item, dict):
                for key, child in item.items():
                    key.encode("utf-8")
                    pending.append((child, depth+1))
            elif isinstance(item, list):
                pending.extend((child, depth+1) for child in item)
        return value
    except (UnicodeError, RecursionError, OverflowError):
        raise ValueError("body contains unsupported JSON encoding, depth or numbers") from None
