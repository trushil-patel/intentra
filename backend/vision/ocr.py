from utils.performance import timed
"""Optional local Tesseract OCR. Screen content is data, never authorization."""
from vision.screen_reader import Observation
from utils.helpers import SafeError


class ScreenReader:
    def __init__(self, observer, store, stop, enabled=False):
        self.observer, self.store, self.stop, self.enabled = observer, store, stop, enabled

    def get_visible_elements(self):
        state = self.observer.observe()
        if not state.available:
            raise SafeError('UI Automation observation unavailable')
        return state.elements

    def summarize_page(self):
        import re
        from utils.logger import redact
        observation = self.read_visible_text()
        # Extractive local summary: no screen text is ever passed to the planner.
        sentences = re.split(r'(?<=[.!?])\s+|\n+', observation.text)
        selected = []
        for sentence in sentences:
            sentence = sentence.strip()
            if sentence and sentence not in selected: selected.append(sentence)
            if len(selected) == 3: break
        return Observation(redact(' '.join(selected)[:600]), source='extractive_summary')

    @timed("ocr")
    def read_regions(self):
        self.stop.check()
        if not self.enabled: raise SafeError('Local OCR is disabled')
        try:
            import pytesseract
            from agent.computer_state import Element
            self.store.capture()
            data = pytesseract.image_to_data(self.store.image, output_type=pytesseract.Output.DICT, timeout=3)
            self.stop.check()
            regions = []
            for index, text in enumerate(data['text'][:200]):
                if not text.strip(): continue
                confidence = float(data['conf'][index]) / 100
                if not 0 <= confidence <= 1: continue
                x, y, width, height = (int(data[key][index]) for key in ('left', 'top', 'width', 'height'))
                if not (0 <= x < x + width <= self.store.image.width and 0 <= y < y + height <= self.store.image.height): continue
                regions.append(Element(('ocr', index), 'Text', text[:200], source='ocr', read_only=True,
                                        bounds=(x, y, x + width, y + height), confidence=confidence))
            return tuple(regions)
        except SafeError:
            raise
        except Exception:
            raise SafeError('Local OCR region extraction failed or timed out') from None
        finally:
            self.store.clear()

    def read_visible_text(self):
        self.stop.check()
        try:
            elements = self.get_visible_elements()
        except SafeError:
            self.stop.check()
            elements = ()
        text = '\n'.join(e.name for e in elements if e.name and not e.password)
        if text:
            return Observation(text[:10000], source='uia')
        if not self.enabled:
            raise SafeError('Local OCR is disabled or structured text unavailable')
        # Explicit request only. No background capture or upload.
        try:
            import pytesseract
            self.store.capture()
            self.stop.check()
            text = pytesseract.image_to_string(self.store.image, timeout=3)
            self.stop.check()
            return Observation(text[:10000], source='ocr')
        except SafeError:
            raise
        except Exception:
            raise SafeError('Local OCR unavailable or timed out') from None
        finally:
            self.store.clear()
