"""IEEE / matpower cases (as pandapower nets) into each hub candidate.

pandapower cases are the *typeless extreme*: generators carry no technology
label at all (the PLEXOS situation from the hub one-pager). What each hub does
with that is the experiment:

  * -> pypsa: the native importer absorbs them as carrier-less generators —
    ingest is effortless, and the missing semantics travel onward silently
    unless the coverage ledger says otherwise (it does, here).
  * -> sienna: every generator must be assigned a (prime_mover, fuel) pair
    from the closed enums RIGHT NOW; with nothing to map from, each one is a
    manual_mapping_required event and lands on (OT, OTHER).

Same source, same missing information — an open hub defers the cost, a closed
hub charges it at the door. The manifests make both visible.
"""

from __future__ import annotations

import math

from ..core import Coverage, Payload, bridge


@bridge("matpower", "pypsa", notes="native pandapower import; typeless gens absorbed")
def matpower_to_pypsa(payload: Payload, opts: dict) -> Payload:
    import pypsa

    net = payload.native
    cov = Coverage(bridge="matpower->pypsa")
    # matpower cases carry blank/duplicate element names, which PyPSA's
    # importer rejects — assign deterministic unique names first
    for table in ("bus", "load", "gen", "sgen", "ext_grid", "line", "trafo"):
        df = getattr(net, table, None)
        if df is None or not len(df):
            continue
        names = df.get("name")
        if names is None or names.isna().any() or names.duplicated().any():
            df["name"] = [f"{table}_{i}" for i in df.index]
    n = pypsa.Network()
    n.import_from_pandapower_net(net)

    total_load = float(net.load["p_mw"].sum()) if len(net.load) else 0.0
    # pandapower's ext_grid arrives as a Slack generator with p_nom = 0;
    # a solvable model needs real capacity behind the slack.
    for gid in n.generators.index:
        row = n.generators.loc[gid]
        if str(row.get("control", "")) == "Slack" and float(row["p_nom"]) <= 0:
            n.generators.loc[gid, "p_nom"] = max(total_load * 1.5, 100.0)
            cov.invented.append({
                "what": f"slack capacity ({gid})",
                "value": float(n.generators.loc[gid, "p_nom"]),
                "why": "ext_grid has no p_nom; sized to 1.5x total load"})

    cov.count("buses", len(n.buses))
    cov.count("lines", len(n.lines))
    cov.count("transformers", len(n.transformers))
    cov.count("generators", len(n.generators))
    cov.count("loads", len(n.loads))
    if len(n.generators):
        cov.approximated.append({
            "what": "generator technology",
            "how": "matpower/pandapower gens are typeless; PyPSA absorbs them "
                   "with an empty carrier — the missing semantics ride along "
                   "silently until some downstream schema needs them"})
    if len(net.shunt):
        cov.dropped.append({"what": f"{len(net.shunt)} shunt(s)",
                            "why": "not carried through this bench's PyPSA leg"})

    out = Payload("pypsa", n, sidecar=payload.sidecar,
                  coverage=list(payload.coverage))
    out.hop(cov)
    return out


@bridge("matpower", "sienna",
        notes="typeless gens must be assigned closed (prime_mover, fuel) at the door")
def matpower_to_sienna(payload: Payload, opts: dict) -> Payload:
    from ..schemas.sienna import (PowerLoad, PrimeMover, SiennaBus, SiennaLine,
                                  SiennaSystem, ThermalFuel, ThermalStandard)

    net = payload.native
    cov = Coverage(bridge="matpower->sienna")
    sys = SiennaSystem(name=str(getattr(net, "name", "case")) or "case")
    sb = float(net.sn_mva) if getattr(net, "sn_mva", None) else 100.0
    sys.base_power_mva = sb

    ref_buses = set(net.ext_grid["bus"].values) if len(net.ext_grid) else set()
    for idx, row in net.bus.iterrows():
        sys.buses.append(SiennaBus(
            name=f"bus_{idx}", number=int(idx),
            base_voltage_kv=float(row["vn_kv"]),
            bus_type="REF" if idx in ref_buses else "PQ"))
        cov.count("buses")

    for idx, row in net.line.iterrows():
        vn = float(net.bus.loc[row["from_bus"], "vn_kv"])
        zbase = vn ** 2 / sb
        length = float(row.get("length_km", 1.0)) or 1.0
        i_max = float(row.get("max_i_ka", 0.0))
        rating = math.sqrt(3) * vn * i_max if i_max > 0 else 0.0
        sys.lines.append(SiennaLine(
            name=f"line_{idx}", from_bus=f"bus_{int(row['from_bus'])}",
            to_bus=f"bus_{int(row['to_bus'])}",
            r_pu=float(row["r_ohm_per_km"]) * length / zbase,
            x_pu=max(float(row["x_ohm_per_km"]) * length / zbase, 1e-5),
            rating_mva=rating))
        cov.count("lines")
    if len(net.trafo):
        for idx, row in net.trafo.iterrows():
            sys.lines.append(SiennaLine(
                name=f"trafo_{idx}", from_bus=f"bus_{int(row['hv_bus'])}",
                to_bus=f"bus_{int(row['lv_bus'])}",
                x_pu=max(float(row["vk_percent"]) / 100.0
                         * sb / max(float(row["sn_mva"]), 1e-6), 1e-5),
                rating_mva=float(row["sn_mva"])))
            cov.count("lines")
        cov.approximated.append({"what": "transformers",
                                 "how": "carried as equivalent lines"})

    total_load = float(net.load["p_mw"].sum()) if len(net.load) else 0.0
    gen_rows = [(f"gen_{i}", int(r["bus"]),
                 float(r.get("max_p_mw", r.get("p_mw", 0.0)) or r.get("p_mw", 0.0)))
                for i, r in net.gen.iterrows()]
    for i, r in net.ext_grid.iterrows():
        gen_rows.append((f"slack_{i}", int(r["bus"]), max(total_load * 1.5, 100.0)))
        cov.invented.append({"what": f"slack capacity (slack_{i})",
                             "value": max(total_load * 1.5, 100.0),
                             "why": "ext_grid has no capacity"})
    for name, bus, pmax in gen_rows:
        # THE closed-taxonomy moment: no type information exists, but the
        # schema demands one. Every generator is a manual-mapping debt.
        sys.thermal.append(ThermalStandard(
            name=name, bus=f"bus_{bus}", prime_mover=PrimeMover.OT,
            fuel=ThermalFuel.OTHER, active_power_limits_max_mw=pmax))
        cov.count("generators")
        cov.manual_mapping_required.append({
            "entity": name, "label": "(none)",
            "note": "typeless source generator: closed schema requires a "
                    "(prime_mover, fuel) pair; defaulted to (OT, OTHER)"})

    for idx, row in net.load.iterrows():
        sys.loads.append(PowerLoad(name=f"load_{idx}",
                                   bus=f"bus_{int(row['bus'])}",
                                   max_active_power_mw=float(row["p_mw"])))
        cov.count("loads")

    out = Payload("sienna", sys, sidecar=payload.sidecar,
                  coverage=list(payload.coverage))
    out.hop(cov)
    return out
