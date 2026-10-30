import json
from safety.policy import Risk
from utils.helpers import SafeError

class Permissions:
    def __init__(self, path=None):
        self.rules = {"LOW": "allow", "MEDIUM": "confirm", "HIGH": "deny"}
        if path is not None:
            rules = json.loads(path.read_text(encoding="utf-8"))
            # Configuration can tighten policy, never weaken its minimums.
            if type(rules) is not dict or set(rules) != set(self.rules):
                raise SafeError("Invalid permissions configuration")
            for risk, allowed in {"LOW": {"allow", "confirm", "deny"}, "MEDIUM": {"confirm", "deny"}, "HIGH": {"deny"}}.items():
                if rules[risk] not in allowed:
                    raise SafeError("Unsafe permissions configuration")
            self.rules = rules

    def evaluate(self, risk):
        return self.rules.get(risk, "deny") if isinstance(risk, Risk) else "deny"
