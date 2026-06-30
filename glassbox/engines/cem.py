"""Capacity expansion engine (PRD Section 6.2) — facets: inv, ops (reduced).

Least-cost investment given a coarsened (typically zonal, representative-period)
view. Linear program built transparently in linopy and solved with HiGHS; the
constraint matrix and duals back explain(). VRE enters as an availability
profile, never a scalar capacity factor — realized capacity factor is an output.
"""

from __future__ import annotations

import numpy as np

from ..explain import ExplainPayload, Formulation
from ..schema import CEMResult, Provenance
from .base import ENGINE_VERSION, Engine
from .economic_core import (
    BuiltModel,
    EconomicView,
    EngineOptions,
    build_dispatch_model,
    collect_dispatch,
    collect_network,
    solve_model,
)


class CapacityExpansionEngine(Engine):
    facets = ["inv", "ops"]
    name = "cem"

    def build(self, view: EconomicView) -> BuiltModel:
        options = EngineOptions(investment=True, unit_commitment=False,
                                reserves=True, label="cem")
        return build_dispatch_model(view, options)

    def solve(self, model: BuiltModel) -> CEMResult:
        status = solve_model(model)
        view = model.view
        result = CEMResult(engine="cem", engine_version=ENGINE_VERSION)

        # Build decisions. Nodal candidates report under their own id; zonal
        # resource-potential tranches (parent_id set) aggregate back to the
        # supply curve they came from. invest_cost is summed straight from the
        # build variables so it stays exact across both channels.
        invest_cost = 0.0
        build_sol = model.m.variables["gen_build"].solution if "gen_build" in model.m.variables else None
        if build_sol is not None:
            for g in view.gens:
                if not g.is_candidate:
                    continue
                mw = float(build_sol.sel(g=g.id))
                invest_cost += mw * g.capex_annual_per_mw
                if mw <= 1e-3:
                    continue
                if g.parent_id:
                    result.built_resource_potential_mw[g.parent_id] = (
                        result.built_resource_potential_mw.get(g.parent_id, 0.0) + mw)
                else:
                    result.built_capacity_mw[g.id] = mw
        if "sto_build_p" in model.m.variables:
            bp = model.m.variables["sto_build_p"].solution
            be = model.m.variables["sto_build_e"].solution
            for s in view.storages:
                if not s.is_candidate:
                    continue
                p_mw = float(bp.sel(s=s.id))
                e_mwh = float(be.sel(s=s.id))
                invest_cost += p_mw * s.capex_annual_per_mw + e_mwh * s.capex_annual_per_mwh
                if p_mw <= 1e-3 and e_mwh <= 1e-3:
                    continue
                if s.parent_id:
                    result.built_resource_potential_mw[s.parent_id] = (
                        result.built_resource_potential_mw.get(s.parent_id, 0.0) + p_mw)
                    result.built_resource_potential_energy_mwh[s.parent_id] = (
                        result.built_resource_potential_energy_mwh.get(s.parent_id, 0.0) + e_mwh)
                else:
                    result.built_storage_power_mw[s.id] = p_mw
                    result.built_storage_energy_mwh[s.id] = e_mwh
        if "line_build" in model.m.variables:
            lb = model.m.variables["line_build"].solution
            for ln in view.lines:
                if not ln.is_candidate:
                    continue
                mw = float(lb.sel(l=ln.id))
                invest_cost += mw * ln.capex_annual_per_mw
                if mw > 1e-3:
                    result.built_transmission_mw[ln.id] = mw

        total = float(model.m.objective.value)
        result.total_cost = total
        result.cost_breakdown = {"investment_annualized": invest_cost,
                                 "operations": total - invest_cost}

        result.operational = collect_dispatch(model)
        result.network = collect_network(model)
        result.provenance = Provenance(
            engine="cem", engine_version=ENGINE_VERSION,
            governing=["min annualized capex+FOM + weighted operating cost",
                       "power balance", "capacity x availability", "policy constraints"],
            notes=f"solver status: {status}")
        return result

    def explain(self, model: BuiltModel, result: CEMResult) -> ExplainPayload:
        view = model.view
        n_cand_g = sum(1 for g in view.gens if g.is_candidate)
        n_cand_s = sum(1 for s in view.storages if s.is_candidate)
        symbolic = [
            "min  Σ_g capex_g·build_g + Σ_s (capex^P_s·buildP_s + capex^E_s·buildE_s)",
            "     + Σ_t weight_t·( Σ_g (mc_g + τ·e_g)·p_{g,t} + VOLL·unserved_t )",
            "s.t. Σ_g p_{g,t} + Σ_s (dis_{s,t}-ch_{s,t}) + net_flow_{n,t} + uns_{n,t} = load_{n,t}",
            "     0 ≤ p_{g,t} ≤ (p_nom_g + build_g)·availability_{g,t}",
            "     soc_{s,t} = soc_{s,t-1} + η_c·ch − dis/η_d   (cyclic per rep period)",
            "     Σ_t w_t·Σ_vre p ≥ rps·load_energy   (if RPS active)",
            "     Σ_t w_t·Σ_g e_g·p_{g,t} ≤ cap   (if emissions cap active)",
        ]
        return ExplainPayload(
            title="Capacity Expansion (LP): least-cost investment + operations",
            formulation=Formulation(
                statement=("Co-optimize what to build and how to run it over "
                           "representative periods. VRE enters as an availability "
                           "profile; realized capacity factor is an output."),
                symbolic=symbolic,
                variables=[f"build_g ({n_cand_g} candidate gens)",
                           f"buildP_s, buildE_s ({n_cand_s} candidate storage)",
                           "p_{g,t}, ch/dis/soc_{s,t}, unserved_{n,t}, flow_{l,t}"],
            ),
            inputs={
                "n_nodes": len(view.nodes), "n_timesteps": view.T,
                "n_gens": len(view.gens), "carbon_price": view.carbon_price,
                "rps_fraction": view.rps_fraction,
                "annual_load_twh": float((view.load.sum(axis=0) * view.period_weight).sum()
                                         / view.annual_divisor / 1e6),
                "candidate_generators": [g.id for g in view.gens if g.is_candidate],
            },
            outputs={
                "total_annual_cost": result.total_cost,
                "cost_breakdown": result.cost_breakdown,
                "built_capacity_mw": result.built_capacity_mw,
                "built_storage_power_mw": result.built_storage_power_mw,
                "built_storage_energy_mwh": result.built_storage_energy_mwh,
                "built_transmission_mw": result.built_transmission_mw,
                "built_resource_potential_mw": result.built_resource_potential_mw,
                "realized_capacity_factor": (result.operational.realized_capacity_factor
                                             if result.operational else {}),
            },
            intermediates={
                "zonal_prices": result.network.nodal_price if result.network else {},
                "binding_interface_duals": result.network.dual_values if result.network else {},
            },
            provenance={"engine": "cem", "version": ENGINE_VERSION,
                        "input_facets": self.facets},
        )
