from agent.schemas import Action


class Recovery:
    """Only idempotent app launch/focus recovery. Never replay typing or submission."""
    def __init__(self, max_retries=2):
        if type(max_retries) is not int or not 0 <= max_retries <= 2:
            raise ValueError("Recovery limit must be between zero and two")
        self.max_retries = max_retries

    def choose(self, action, state, attempts):
        if attempts >= self.max_retries or not state.available or action.tool not in {"open_app", "focus_app"}:
            return None
        matches = [w for w in state.open_windows if w.app == action.arguments["app_name"]]
        if len(matches) == 1:
            return Action("focus_app", dict(action.arguments))
        if not matches:
            return Action("open_app", dict(action.arguments))
        return None
