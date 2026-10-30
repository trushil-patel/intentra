class SafeError(Exception):
    """A fixed, non-sensitive diagnostic safe to show to the user."""

class Cancelled(SafeError):
    pass
