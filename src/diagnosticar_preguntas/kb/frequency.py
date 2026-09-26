"""Parsing of the ``frequency`` column of ``phenotype.hpoa``.

The column holds one of three notations: an HPO frequency term, a cohort
fraction ``n/m`` or a percentage ``x%``. Frequency terms stand for ranges, so
each maps to the midpoint of its range as defined by the HPO. An empty cell
stays ``None``: it means "reported, frequency unknown", which is not the same
as "always present". The value used for unknown frequencies is a separate,
explicit prior of the likelihood model, never baked into the knowledge base.
"""

from __future__ import annotations

import re

OBLIGATE = "HP:0040280"
VERY_FREQUENT = "HP:0040281"
FREQUENT = "HP:0040282"
OCCASIONAL = "HP:0040283"
VERY_RARE = "HP:0040284"
EXCLUDED = "HP:0040285"

FREQUENCY_CATEGORIES: dict[str, float] = {
    OBLIGATE: 1.0,
    VERY_FREQUENT: (0.80 + 0.99) / 2,
    FREQUENT: (0.30 + 0.79) / 2,
    OCCASIONAL: (0.05 + 0.29) / 2,
    VERY_RARE: (0.01 + 0.04) / 2,
    EXCLUDED: 0.0,
}

_FRACTION = re.compile(r"^(\d+)\s*/\s*(\d+)$")
_PERCENT = re.compile(r"^(\d+(?:\.\d+)?)\s*%$")


class FrequencyError(ValueError):
    pass


def parse_frequency(raw: str) -> float | None:
    """Return the frequency as a probability in [0, 1], or ``None`` when not stated."""
    text = raw.strip()
    if not text or text == "-":
        return None
    if text in FREQUENCY_CATEGORIES:
        return FREQUENCY_CATEGORIES[text]
    if match := _FRACTION.match(text):
        numerator, denominator = int(match[1]), int(match[2])
        if denominator == 0 or numerator > denominator:
            raise FrequencyError(f"invalid fraction {raw!r}")
        return numerator / denominator
    if match := _PERCENT.match(text):
        value = float(match[1]) / 100
        if value > 1:
            raise FrequencyError(f"invalid percentage {raw!r}")
        return value
    raise FrequencyError(f"unrecognised frequency {raw!r}")
