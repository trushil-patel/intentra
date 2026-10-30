"""Best-effort local redaction; incomplete structured coverage denies AI processing."""
import re
from PIL import ImageDraw
from utils.helpers import SafeError

SENSITIVE_REGION = re.compile(r'(?i)password|passwd|credit.?card|authentication|verification code|one.?time|secret|token|api.?key')


def redact_screen(image, screen):
    if not screen.computer.available or not screen.computer.elements_complete:
        raise SafeError('Cannot redact an incomplete screen observation')
    redacted = image.copy()
    try:
        draw = ImageDraw.Draw(redacted)
        for reference in screen.elements:
            element = reference.element
            if not element.password and not SENSITIVE_REGION.search(element.name): continue
            if len(element.bounds) != 4:
                raise SafeError('Sensitive region bounds unavailable; vision denied')
            left, top, right, bottom = element.bounds
            if right <= left or bottom <= top:
                raise SafeError('Sensitive region bounds invalid; vision denied')
            # Coordinates are observed desktop coordinates; require primary-screen coverage.
            if left < 0 or top < 0 or right > image.width or bottom > image.height:
                raise SafeError('Sensitive region outside capture bounds; vision denied')
            draw.rectangle((left, top, right, bottom), fill='black')
        return redacted
    except Exception:
        redacted.close()
        raise
