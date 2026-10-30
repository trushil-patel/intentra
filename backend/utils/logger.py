"""Data minimization: no commands, arguments, observations or exception messages."""
import json
import logging
import re
from logging.handlers import RotatingFileHandler
from uuid import uuid4

def redact(value: str) -> str:
    value = re.sub(r"-----BEGIN .*?PRIVATE KEY-----.*?-----END .*?PRIVATE KEY-----", "[REDACTED]", value, flags=re.S)
    value = re.sub(r"(?i)\b(password|passwd|secret|token|api[_ -]?key|cookie|authorization)\b[^\r\n]*", "[REDACTED]", value)
    value = re.sub(r"\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]+", "[REDACTED]", value)
    return re.sub(r"\b(?:\d[ -]?){13,19}\b", "[REDACTED]", value)

class FailClosedHandler(RotatingFileHandler):
    def handleError(self, record):
        from utils.helpers import SafeError
        raise SafeError("Audit log unavailable; execution denied") from None

class Audit:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.session_id = uuid4().hex
        self.task_id = None
        self.action_id = None
        self.logger = logging.getLogger("voxpilot." + self.session_id)
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        handler = FailClosedHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
        self.logger.addHandler(handler)

    def event(self, tool, risk, status):
        # Callers provide canonical registry names and fixed outcome codes only.
        from tools.registry import SPECS
        tool = tool if tool in SPECS else "unrecognized"
        risk = risk if risk in {"LOW", "MEDIUM", "HIGH"} else "UNKNOWN"
        status = status if status in {"ALLOWED", "BLOCKED", "SUCCESS", "ERROR", "CANCELLED"} else "ERROR"
        payload = dict(session=self.session_id, tool=tool, risk=risk, status=status)
        if isinstance(self.task_id, str) and re.fullmatch(r"[a-f0-9]{32}", self.task_id):
            payload["task"] = self.task_id
            payload["action"] = self.action_id if type(self.action_id) is int and 0 <= self.action_id < 30 else -1
        self.logger.info(json.dumps(payload))

    def task_event(self, task_id, action_id, tool, risk, verification, duration):
        from tools.registry import SPECS
        payload = dict(session=self.session_id,
                       task=task_id if isinstance(task_id, str) and re.fullmatch(r"[a-f0-9]{32}", task_id) else "unknown",
                       action=action_id if type(action_id) is int and 0 <= action_id < 30 else -1,
                       tool=tool if tool in SPECS else "unrecognized",
                       risk=risk if risk in {"LOW", "MEDIUM", "HIGH"} else "UNKNOWN",
                       validation="PASSED", execution="SUCCESS",
                       verification=verification if verification in {"VERIFIED", "FAILED", "UNCERTAIN"} else "UNCERTAIN",
                       duration_ms=round(min(max(duration, 0), 3600) * 1000) if type(duration) in {float, int} else 0)
        self.logger.info(json.dumps(payload))

    def close(self):
        for handler in self.logger.handlers[:]:
            handler.close()
            self.logger.removeHandler(handler)
