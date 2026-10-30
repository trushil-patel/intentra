"""Retain provenance; lower-trust boxes cannot overwrite UIA identity."""
def overlap(a, b):
    if len(a) != 4 or len(b) != 4: return 0
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    denominator = min(max(0, a[2]-a[0])*max(0, a[3]-a[1]), max(0, b[2]-b[0])*max(0, b[3]-b[1]))
    return intersection / denominator if denominator else 0


def merge(primary, secondary):
    merged = list(primary)
    for element in secondary:
        if any(e.name.casefold() == element.name.casefold() and overlap(e.bounds, element.bounds) >= .7 for e in merged):
            continue
        merged.append(element)
    return tuple(merged[:300])
