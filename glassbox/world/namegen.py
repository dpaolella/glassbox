"""Deterministic place/plant name generator (issue #26).

Real planning maps name things — "Millbrook CCGT", "Port Alder" — and named
entities make every downstream table, tooltip, and result more readable and
memorable than ``gen_7``. Names are generated once at world build from the
world seed, so they are stable and inspectable like everything else.
"""

from __future__ import annotations

import random

# small curated word banks; combinations give ~thousands of plausible names
_PREFIX = [
    "Alder", "Birch", "Cedar", "Drift", "Elk", "Fern", "Gran", "Haven",
    "Iron", "Juniper", "Kestrel", "Lark", "Mill", "North", "Oak", "Pike",
    "Quarry", "Ridge", "Stone", "Thorn", "Vale", "Willow", "Wolf", "Amber",
    "Bright", "Clear", "Dun", "Ester", "Frost", "Gold", "Harrow", "Ing",
]
_SUFFIX_CITY = [
    "field", "ford", "haven", "mont", "port", "ridge", "ton", "vale",
    "view", "water", "bury", "brook", "crest", "dale", "gate", "moor",
]
_SUFFIX_SUB = ["Junction", "Crossing", "Switchyard", "Substation", "Tap"]

_PLANT_STYLE = {
    "nuclear": ["{} Point Nuclear Station", "{} Nuclear Generating Station"],
    "coal": ["{} Steam Plant", "{} Generating Station"],
    "ccgt": ["{} Energy Center", "{} Combined Cycle"],
    "ocgt": ["{} Peakers", "{} Gas Turbines"],
    "wind": ["{} Wind Farm", "{} Wind Energy Center"],
    "solar_pv": ["{} Solar Ranch", "{} Solar Park"],
    "battery": ["{} Storage Center", "{} BESS"],
    "reservoir": ["{} Dam", "{} Hydroelectric Project"],
    "run_of_river": ["{} Falls Hydro", "{} River Station"],
    "pumped": ["{} Pumped Storage", "{} Upper Reservoir"],
}


class NameGenerator:
    """Seeded generator handing out unique city / substation / plant names."""

    def __init__(self, seed: int):
        self._rng = random.Random(seed * 7919 + 17)
        self._used: set[str] = set()

    def _unique(self, make) -> str:
        for _ in range(200):
            name = make()
            if name not in self._used:
                self._used.add(name)
                return name
        # fall back to numbered variant rather than loop forever
        base = make()
        i = 2
        while f"{base} {i}" in self._used:
            i += 1
        self._used.add(f"{base} {i}")
        return f"{base} {i}"

    def city(self) -> str:
        r = self._rng
        return self._unique(
            lambda: ("Port " if r.random() < 0.18 else "")
            + r.choice(_PREFIX) + r.choice(_SUFFIX_CITY))

    def substation(self, near_city: str | None = None) -> str:
        r = self._rng
        stem = (near_city.replace("Port ", "").split()[0]
                if near_city else r.choice(_PREFIX))
        return self._unique(lambda: f"{stem} {r.choice(_SUFFIX_SUB)}")

    def plant(self, technology: str, near_city: str | None = None) -> str:
        r = self._rng
        styles = _PLANT_STYLE.get(technology, ["{} Power Plant"])
        stem_bank = _PREFIX if near_city is None else [
            near_city.replace("Port ", "").split()[0], r.choice(_PREFIX)]
        return self._unique(
            lambda: r.choice(styles).format(r.choice(stem_bank)))
