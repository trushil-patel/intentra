import json
import time
from collections import Counter, deque
from utils.helpers import SafeError

class RateLimiter:
    def __init__(self, max_actions=10, clock=time.monotonic):
        self.maximum = min(max_actions, 10)
        self.clock = clock
        self.clicks = deque()
        self.actions = deque()

    def check_plan(self, actions):
        if not 1 <= len(actions) <= self.maximum:
            raise SafeError("Plan action limit exceeded")
        counts = Counter((a.tool, json.dumps(a.arguments, sort_keys=True)) for a in actions)
        if max(counts.values()) > 3:
            raise SafeError("Repeated action limit exceeded")

    def consume(self, action):
        now = self.clock()
        for queue in (self.actions, self.clicks):
            while queue and now - queue[0] >= 10:
                queue.popleft()
        cost = 2 if action.tool == "double_click" else 1
        if len(self.actions) >= 20:
            raise SafeError("Action rate limit exceeded")
        if action.tool in {"click", "double_click", "right_click", "click_element"}:
            if len(self.clicks) + cost > 5:
                raise SafeError("Click rate limit exceeded")
            self.clicks.extend([now] * cost)
        self.actions.append(now)
