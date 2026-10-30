"""Parent-owned cancellation; children never own interactive console shutdown."""
import signal


def configure_worker():
    # The parent receives console Ctrl+C and runs bounded cleanup. Children are
    # stopped via their IPC/event/deadline, not an asynchronous Python exception.
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def send_reply(connection, value):
    """A parent disappearing during cancellation is a normal closed IPC channel."""
    try:
        connection.send(value)
        return True
    except (EOFError, OSError):
        return False
