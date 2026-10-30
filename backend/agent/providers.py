import json
from typing import Protocol
from agent.prompts import SYSTEM, plan_schema
from config.settings import Settings
from utils.helpers import SafeError

class LLMProvider(Protocol):
    def generate_plan(self, command: str, context: dict) -> str: ...

class OllamaProvider:
    def __init__(self, settings: Settings, session=None):
        self.settings = settings
        if session is None:
            import requests
            session = requests.Session()
        self.session = session
        self.session.trust_env = False

    def generate_plan(self, command, context):
        # Whitelist deterministic app identities; never pass screen text, titles or OCR.
        app = context.get("current_application")
        safe_context = {"current_application": app} if type(app) is str and app in {"chrome", "edge", "firefox", "notepad", "vscode"} else {}

        payload = {"model": self.settings.ollama_model, "stream": False,
                   "format": plan_schema(), "options": {"temperature": 0, "num_predict": 2048},
                   "messages": [{"role": "system", "content": SYSTEM + "\nSession app context: " + json.dumps(safe_context)}, {"role": "user", "content": command}]}
        try:
            with self.session.post(self.settings.ollama_url.rstrip("/") + "/api/chat", json=payload,
                                   timeout=(3, 45), allow_redirects=False, stream=True) as response:
                if response.status_code != 200:
                    raise SafeError("Local Ollama unavailable or model missing")
                chunks = bytearray()
                for chunk in response.iter_content(4096):
                    chunks.extend(chunk)
                    if len(chunks) > 65536:
                        raise SafeError("Ollama response exceeded limit")
                data = json.loads(chunks)
                text = data["message"]["content"]
                if type(text) is not str:
                    raise ValueError()
                return text
        except SafeError:
            raise
        except Exception:
            raise SafeError("Local Ollama planning failed; no actions executed") from None

    def close(self):
        self.session.close()
