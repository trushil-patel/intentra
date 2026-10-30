"""Strict JSON decoding, including rejection of duplicate keys."""
import json
from dataclasses import dataclass
from utils.helpers import SafeError

@dataclass(frozen=True)
class Action:
    tool: str
    arguments: dict

@dataclass(frozen=True)
class Plan:
    goal: str
    actions: tuple[Action, ...]

    @classmethod
    def parse(cls, raw: str):
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise SafeError("Duplicate JSON field")
                result[key] = value
            return result
        try:
            if len(raw) > 32768:
                raise SafeError("Plan too large")
            data = json.loads(raw, object_pairs_hook=unique)
            if type(data) is not dict or set(data) != {"goal", "actions"}:
                raise ValueError()
            if type(data["goal"]) is not str or len(data["goal"]) > 500:
                raise ValueError()
            actions = data["actions"]
            if type(actions) is not list or not 1 <= len(actions) <= 10:
                raise ValueError()
            result = []
            for item in actions:
                if type(item) is not dict or set(item) != {"tool", "arguments"}:
                    raise ValueError()
                if type(item["tool"]) is not str or type(item["arguments"]) is not dict:
                    raise ValueError()
                result.append(Action(item["tool"], item["arguments"]))
            return cls(data["goal"], tuple(result))
        except (ValueError, TypeError, KeyError, RecursionError):
            raise SafeError("Malformed plan; please repeat or clarify") from None
