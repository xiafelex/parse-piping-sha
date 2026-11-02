"""Shared proportional sizing for text inside fixed four-character weld tags."""


def weld_label_scale(number: str) -> float:
    """Keep ordinary labels unchanged; reduce longer labels without squeezing."""
    return min(1.0, 4.0 / max(1, len(number.strip())))
