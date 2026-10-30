"""Nonblocking optional telemetry. Never executes or authorizes actions."""
_sink = None


def install(sink):
    global _sink
    _sink = sink


def emit(status, kind, message='', payload=None, new_session=False):
    if _sink is not None:
        # Observability must not change the existing engine's behavior.
        try:
            _sink.emit(status, kind, message, payload, new_session)
        except Exception:
            _sink.failed.set()
