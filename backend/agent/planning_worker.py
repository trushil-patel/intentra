from utils.performance import timed
"""Killable local planning client; proposal output still goes through Plan/Router."""
import multiprocessing
import time
from utils.helpers import SafeError


def _worker(connection, settings, command, context):
    from utils.worker_lifecycle import configure_worker, send_reply
    configure_worker()
    from agent.providers import OllamaProvider
    provider = None
    try:
        provider = OllamaProvider(settings)
        send_reply(connection, ('ok', provider.generate_plan(command, context)))
    except Exception:
        send_reply(connection, ('error', None))
    finally:
        if provider: provider.close()
        connection.close()


class BoundedOllamaProvider:
    def __init__(self, settings, stop, timeout=45):
        self.settings, self.stop, self.timeout = settings, stop, timeout

    @timed("ollama")
    def generate_plan(self, command, context):
        self.stop.check()
        ctx = multiprocessing.get_context('spawn')
        reader, writer = ctx.Pipe(duplex=False)
        process = ctx.Process(target=_worker, args=(writer, self.settings, command, context))
        try:
            process.start()
            writer.close()
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                self.stop.check()
                if reader.poll(.025):
                    kind, value = reader.recv()
                    self.stop.check()
                    if kind == 'ok' and type(value) is str and len(value) <= 32768: return value
                    raise SafeError('Local planning failed; no actions executed')
                if not process.is_alive(): break
            raise SafeError('Local planning timed out; no actions executed')
        except (EOFError, OSError):
            raise SafeError('Local planning unavailable; no actions executed') from None
        finally:
            if process.pid:
                if process.is_alive(): process.terminate()
                process.join(.5)
                if process.is_alive():
                    process.kill()
                    process.join(.5)
                if not process.is_alive(): process.close()
            reader.close()
            writer.close()

    def close(self):
        pass
