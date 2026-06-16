"""Schema + facet introspection tests (PRD Sections 4.2, 2.5)."""

from __future__ import annotations

import pytest

from glassbox.schema import (
    ENTITY_MODELS,
    Facet,
    Generator,
    SynchronousMachineModel,
    all_facets_for,
    field_metadata,
    fields_in_facet,
)


def test_every_entity_has_core_identity():
    for name, model in ENTITY_MODELS.items():
        assert "id" in model.model_fields, f"{name} missing id"
        core = fields_in_facet(model, Facet.CORE)
        assert "id" in core, f"{name}.id must be tagged core"


def test_generator_facet_partitioning():
    # The hallmark example: the same object reveals different depth per layer.
    ops = fields_in_facet(Generator, "ops")
    inv = fields_in_facet(Generator, "inv")
    dyn = fields_in_facet(Generator, "dyn")
    assert "heat_rate_mmbtu_per_mwh" in ops
    assert "ramp_up_mw_per_min" in ops
    assert "capex_per_mw" in inv
    assert "build_max_mw" in inv
    assert "dynamic_model_id" in dyn
    # ops must NOT leak investment-only fields
    assert "capex_per_mw" not in ops


def test_field_metadata_has_units_and_facets():
    md = field_metadata(SynchronousMachineModel)
    assert md["h_s"]["facets"] == ["dyn"]
    assert md["h_s"]["unit"] == "s"
    # machine-base reactances carry base metadata (Section 4.3)
    assert md["xd"]["base"] == "machine_mva"


def test_all_facets_for_orders_canonically():
    facets = all_facets_for(Generator)
    order = [f.value for f in Facet]
    assert facets == [f for f in order if f in facets]


@pytest.mark.parametrize("facet", [f.value for f in Facet])
def test_fields_in_facet_returns_known_fields(facet):
    for model in ENTITY_MODELS.values():
        names = fields_in_facet(model, facet)
        for n in names:
            assert n in model.model_fields
