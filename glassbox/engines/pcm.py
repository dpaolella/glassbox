"""Production cost engine (PRD Section 6.3) — facets: ops, core.

Chronological unit commitment (MILP) + economic dispatch (LP) over a horizon at
hourly resolution, nodal or zonal. The objective minimizes operating cost plus
unserved energy at VOLL. Because a MILP has no useful duals, we solve the MILP,
fix the commitment, and re-solve the LP to read locational marginal prices — a
standard, teachable technique. Network limits use a transparent DC power-flow
(angle) formulation in the nodal case, transport in the zonal case.
"""

from __future__ import annotations

import numpy as np

from ..explain import ExplainPayload, Formulation
from ..schema import PCMResult, Provenance
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


class ProductionCostEngine(Engine):
    facets = ["ops", "core"]
    name = "pcm"

    def build(self, view: EconomicView) -> BuiltModel:
        options = EngineOptions(investment=False, unit_commitment=True,
                                reserves=True, label="pcm")
        return build_dispatch_model(view, options)

    def solve(self, model: BuiltModel) -> PCMResult:
        # 1) MILP unit commitment
        status = solve_model(model)
        result = PCMResult(engine="pcm", engine_version=ENGINE_VERSION,
                           solve_status=status)
        result.objective = float(model.m.objective.value)
        result.dispatch = collect_dispatch(model)

        # 2) fix commitment, re-solve LP for prices (LMPs from balance duals)
        uc_ids = model.meta.get("uc_ids", [])
        if uc_ids and "commit" in model.m.variables:
            commit_sol = model.m.variables["commit"].solution
            fixed = {}
            for gid in uc_ids:
                arr = np.round(commit_sol.sel(g=gid).values).astype(float)
                fixed[gid] = arr
            lp_options = EngineOptions(investment=False, unit_commitment=True,
                                       reserves=True, label="pcm_lp",
                                       fixed_commitment=fixed)
            lp_model = build_dispatch_model(model.view, lp_options)
            solve_model(lp_model)
            result.network = collect_network(lp_model)
        else:
            result.network = collect_network(model)

        result.provenance = Provenance(
            engine="pcm", engine_version=ENGINE_VERSION,
            governing=["min operating cost + VOLL·unserved",
                       "commitment min up/down + startup logic", "ramp limits",
                       "DC power flow / transport limits", "interface limits"],
            notes=f"MILP status {status}; LMPs from fixed-commitment LP duals")
        return result

    def explain(self, model: BuiltModel, result: PCMResult) -> ExplainPayload:
        view = model.view
        uc_ids = model.meta.get("uc_ids", [])
        net_term = ("flow_{l,t} = (θ_a − θ_b)/x_l,  |flow| ≤ rating_l   (DC power flow)"
                    if view.network_mode == "dc"
                    else "|flow_{corridor,t}| ≤ NTC_corridor   (transport)")
        symbolic = [
            "min  Σ_t w_t·( Σ_g (mc_g + τ·e_g)·p_{g,t} + Σ_g (startup_g·su + noload_g·u)",
            "             + VOLL·Σ_n uns_{n,t} )",
            "s.t. Σ_g p_{g,t} + Σ_s (dis−ch) + net_flow + uns = load   (dual = LMP)",
            "     u_{g,t}·pmin_g ≤ p_{g,t} ≤ u_{g,t}·pmax_g",
            "     u_{g,t} − u_{g,t−1} = su_{g,t} − sd_{g,t}",
            "     Σ_{k=t−Lup+1}^{t} su_{g,k} ≤ u_{g,t};  Σ sd ≤ 1 − u",
            "     |p_{g,t} − p_{g,t−1}| ≤ ramp_g",
            "     " + net_term,
        ]
        # congestion summary
        congested = {k: v for k, v in (result.network.dual_values if result.network else {}).items()}
        price_spread = 0.0
        if result.network and result.network.nodal_price:
            prices = list(result.network.nodal_price.values())
            price_spread = float(max(prices) - min(prices))
        return ExplainPayload(
            title="Production Cost (MILP UC + LP ED): chronological dispatch & prices",
            formulation=Formulation(
                statement=("Commit and dispatch units hour by hour to meet load "
                           "at least cost, respecting min up/down, ramps and "
                           "network limits. LMPs are the duals of nodal balance."),
                symbolic=symbolic,
                variables=[f"u/su/sd_{{g,t}} ({len(uc_ids)} committed thermal units)",
                           "p_{g,t}, ch/dis/soc_{s,t}, flow_{l,t}, θ_{n,t}, uns_{n,t}"],
            ),
            inputs={
                "network_mode": view.network_mode,
                "n_nodes": len(view.nodes), "n_timesteps": view.T,
                "n_committed_units": len(uc_ids), "carbon_price": view.carbon_price,
                "peak_load_mw": float(view.load.sum(axis=0).max()),
            },
            outputs={
                "objective_operating_cost": result.objective,
                "solve_status": result.solve_status,
                "nodal_prices": result.network.nodal_price if result.network else {},
                "price_spread": price_spread,
                "realized_capacity_factor": (result.dispatch.realized_capacity_factor
                                             if result.dispatch else {}),
                "unserved_nodes": list(result.dispatch.unserved_mw.keys())
                if result.dispatch else [],
            },
            intermediates={
                "binding_interfaces": congested,
                "branch_flows_avg_mw": result.network.flow_mw if result.network else {},
            },
            provenance={"engine": "pcm", "version": ENGINE_VERSION,
                        "input_facets": self.facets},
        )
