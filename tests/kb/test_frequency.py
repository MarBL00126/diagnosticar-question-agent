from __future__ import annotations

import pytest

from diagnosticar_preguntas.kb.frequency import FrequencyError, parse_frequency


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HP:0040280", 1.0),
        ("HP:0040281", 0.895),
        ("HP:0040282", 0.545),
        ("HP:0040283", 0.17),
        ("HP:0040284", 0.025),
        ("HP:0040285", 0.0),
        ("3/4", 0.75),
        ("0/5", 0.0),
        ("12/12", 1.0),
        ("15%", 0.15),
        ("7.5%", 0.075),
        (" 1/2 ", 0.5),
    ],
)
def test_stated_frequencies_map_to_a_probability(raw: str, expected: float) -> None:
    assert parse_frequency(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["", "  ", "-"])
def test_unstated_frequency_stays_none_instead_of_certain(raw: str) -> None:
    assert parse_frequency(raw) is None


@pytest.mark.parametrize("raw", ["5/3", "1/0", "150%", "often", "HP:0000118"])
def test_malformed_frequency_is_rejected(raw: str) -> None:
    with pytest.raises(FrequencyError):
        parse_frequency(raw)
