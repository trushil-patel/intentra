"""Explicit observation levels; screenshots never occur at normal UIA level."""
from dataclasses import replace
from enum import IntEnum
from vision.semantic import ObservationCache, ScreenState
from vision.merger import merge
from utils.helpers import SafeError


class ObservationLevel(IntEnum):
    METADATA = 0
    ACCESSIBILITY = 1
    OCR = 2
    VISION = 3


class ObservationEngine:
    def __init__(self, observer, reader=None, store=None, vision=None):
        self.observer, self.reader, self.store, self.vision = observer, reader, store, vision
        self.cache = ObservationCache()

    def observe(self, level=ObservationLevel.ACCESSIBILITY, *, expected_text=None, target=None, roles=None):
        if not isinstance(level, ObservationLevel): raise SafeError('Invalid observation level')
        arguments = {'expected_text': expected_text,
                     'level': int(min(level, ObservationLevel.ACCESSIBILITY))}
        if target is not None:
            arguments['target'] = target
        if roles is not None:
            arguments['roles'] = roles
        state = self.observer.observe(**arguments)
        if level >= ObservationLevel.OCR:
            if not self.reader: raise SafeError('OCR is not configured')
            state = replace(state, elements=merge(state.elements, self.reader.read_regions()))
        if level == ObservationLevel.VISION:
            if not self.vision or not self.store: raise SafeError('Vision is not configured')
            try:
                self.store.capture()
                result = self.vision.analyze_screen(self.store.image, ScreenState.from_computer(state))
                state = replace(state, elements=merge(state.elements, result.regions))
            finally:
                self.store.clear()
        return self.cache.put(state).computer

    def invalidate(self):
        self.cache.invalidate()
