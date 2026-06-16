"""Phenomena-checklist completeness (PRD Sections 11.3, 1.5).

Confirms every documented phenomenon maps to a real Scenario Lab preset (where it
claims one) and that all six modeling layers are covered — the fidelity
completeness check that "every learning objective maps to a concrete in-tool
demonstration."
"""

from __future__ import annotations

import warnings

from fastapi.testclient import TestClient

from glassbox.api import app
from glassbox.validation import PHENOMENA, phenomena_by_layer

warnings.filterwarnings("ignore")
client = TestClient(app)


def test_all_layers_covered():
    assert set(phenomena_by_layer()) == {"cem", "pcm", "ra", "pf", "dyn", "emt"}


def test_referenced_presets_exist():
    preset_keys = {p["key"] for p in client.get("/api/scenario/presets").json()}
    for ph in PHENOMENA:
        if ph.preset_key is not None:
            assert ph.preset_key in preset_keys, f"missing preset {ph.preset_key}"


def test_every_phenomenon_names_a_test():
    for ph in PHENOMENA:
        assert "::" in ph.test and ph.test.startswith("test_")
