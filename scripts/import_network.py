"""Import a real network into a Glassbox world (issue #32).

Converts a pandapower case (which includes every MATPOWER/IEEE case shipped
with pandapower — case14, case30, case118, ...) into a Glassbox ``World``:

    python scripts/import_network.py case14
    python scripts/import_network.py case118 --out data/case118

What you get: buses (with geodata when the case has it, else a spring
layout), lines and transformers converted to the system per-unit base, zones
(from the case's zone column, else a simple k-means on coordinates),
generators with technology/cost defaults inferred from size (documented as
assumptions — the inspector shows them honestly), loads with synthesized
Glassbox weather profiles scaled to the case's MW, and a slack reference.

Honest limitations (v1): no dynamics data (dyn/EMT layers need hand-added
models), costs are typical defaults not case data, shunts/impedances beyond
lines+trafos are dropped, and AC power-flow convergence is not guaranteed on
imported impedance data (the DC-based economic layers are the primary use —
verified: case14 PCM solves cleanly).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from glassbox.schema import (  # noqa: E402
    ACLine, Bus, BusType, Fuel, Generator, GenTechnology, Load, Policy,
    PolicyKind, ReserveKind, ReserveProduct, Transformer, WeatherModelParams,
    WeatherSite, World, Zone,
)
from glassbox.weather.generator import WeatherGenerator  # noqa: E402
from glassbox.world.namegen import NameGenerator  # noqa: E402
from glassbox.world.serialize import save_world  # noqa: E402

# size-based technology defaults: (min MW, tech, fuel, heat rate, vom, pmin)
_TECH_BY_SIZE = [
    (400.0, GenTechnology.NUCLEAR, "uranium", 10.4, 2.0, 0.7),
    (150.0, GenTechnology.COAL, "coal", 9.5, 4.0, 0.4),
    (60.0, GenTechnology.CCGT, "gas", 6.8, 3.0, 0.4),
    (0.0, GenTechnology.OCGT, "gas", 10.5, 8.0, 0.2),
]


def import_case(case: str, out: Path, seed: int = 7, n_years: int = 3) -> None:
    import pandapower.networks as pn

    maker = getattr(pn, case, None)
    if maker is None:
        raise SystemExit(f"pandapower.networks has no case '{case}'")
    net = maker()
    t0 = time.time()

    S = float(net.sn_mva) if getattr(net, "sn_mva", None) else 100.0
    w = World(id=case, name=f"{case} (imported)",
              description=f"Imported from pandapower.networks.{case}; "
                          "costs/technologies are size-based defaults.",
              base_power_mva=S, base_frequency_hz=float(net.f_hz or 60.0))

    # --- coordinates: case geodata, else a spring layout over the graph ---
    coords: dict[int, tuple[float, float]] = {}
    geo = getattr(net, "bus_geodata", None)
    if geo is not None and len(geo):
        for idx, row in geo.iterrows():
            coords[idx] = (float(row.x) * 100.0, -float(row.y) * 100.0)
    if len(coords) < len(net.bus):
        import networkx as nx

        g = nx.Graph()
        g.add_nodes_from(net.bus.index)
        for _, ln in net.line.iterrows():
            g.add_edge(int(ln.from_bus), int(ln.to_bus))
        for _, tr in net.trafo.iterrows():
            g.add_edge(int(tr.hv_bus), int(tr.lv_bus))
        pos = nx.spring_layout(g, seed=seed, scale=400.0)
        coords = {int(i): (float(p[0]) + 500, float(p[1]) + 500)
                  for i, p in pos.items()}

    # --- zones: case zone column when present, else coordinate clusters ---
    if "zone" in net.bus.columns and net.bus["zone"].nunique() > 1:
        zone_of = {int(i): f"Z{int(z)}" for i, z in net.bus["zone"].items()}
    else:
        n_zones = max(1, min(4, len(net.bus) // 8))
        pts = np.array([coords[int(i)] for i in net.bus.index])
        rng = np.random.default_rng(seed)
        centers = pts[rng.choice(len(pts), n_zones, replace=False)]
        for _ in range(12):  # tiny k-means
            d = ((pts[:, None, :] - centers[None]) ** 2).sum(-1)
            lab = d.argmin(1)
            for k in range(n_zones):
                if (lab == k).any():
                    centers[k] = pts[lab == k].mean(0)
        zone_of = {int(i): f"Z{int(lab[j]) + 1}"
                   for j, i in enumerate(net.bus.index)}

    ng = NameGenerator(seed)
    slack_buses = set(int(b) for b in net.ext_grid.bus)
    load_buses = set(int(b) for b in net.load.bus)
    bid = {int(i): f"B{int(i)}" for i in net.bus.index}
    for i, row in net.bus.iterrows():
        i = int(i)
        w.buses.append(Bus(
            id=bid[i],
            name=ng.city() if i in load_buses else ng.substation(),
            base_kv=float(row.vn_kv), zone_id=zone_of[i],
            x=coords[i][0], y=coords[i][1],
            bus_type=BusType.SLACK if i in slack_buses else BusType.PQ))
    zones = sorted(set(zone_of.values()))
    for z in zones:
        w.zones.append(Zone(id=z, name=f"Area {z[1:]}",
                            member_bus_ids=[bid[i] for i, zz in zone_of.items()
                                            if zz == z]))
    w.reference_bus_id = bid[next(iter(slack_buses))] if slack_buses else w.buses[0].id

    # --- branches (per-unit on the system base) ---
    for i, ln in net.line.iterrows():
        vn = float(net.bus.at[int(ln.from_bus), "vn_kv"])
        zb = vn ** 2 / S
        L = float(ln.length_km)
        rating = float(ln.max_i_ka) * vn * np.sqrt(3) if ln.max_i_ka else 250.0
        w.ac_lines.append(ACLine(
            id=f"L{int(i)}", name=f"L{int(i)}",
            from_bus_id=bid[int(ln.from_bus)], to_bus_id=bid[int(ln.to_bus)],
            r=float(ln.r_ohm_per_km) * L / zb, x=float(ln.x_ohm_per_km) * L / zb,
            b=2 * np.pi * w.base_frequency_hz * float(ln.c_nf_per_km) * 1e-9 * L * zb,
            length_km=max(L, 1.0), rating_normal_mva=rating,
            rating_emergency_mva=rating * 1.2, rating_lt_mva=rating * 1.1))
    for i, tr in net.trafo.iterrows():
        zk = float(tr.vk_percent) / 100.0 * S / float(tr.sn_mva)
        rk = float(tr.vkr_percent) / 100.0 * S / float(tr.sn_mva)
        w.transformers.append(Transformer(
            id=f"T{int(i)}", name=f"T{int(i)}",
            from_bus_id=bid[int(tr.hv_bus)], to_bus_id=bid[int(tr.lv_bus)],
            r=rk, x=max(np.sqrt(max(zk ** 2 - rk ** 2, 1e-8)), 1e-4),
            rating_mva=float(tr.sn_mva)))

    # --- generators (ext_grid becomes a large slack unit) ---
    gens = [(int(g.bus), float(g.p_mw), float(g.max_p_mw)
             if not np.isnan(g.get("max_p_mw", np.nan)) else float(g.p_mw) * 1.5)
            for _, g in net.gen.iterrows()]
    total_load = float(net.load.p_mw.sum())
    for b in slack_buses:
        gens.append((b, total_load * 0.4, total_load * 0.8))
    for k, (b, p, pmax) in enumerate(gens):
        pmax = max(pmax, p, 10.0)
        tech, fuel, hr, vom, pmin = next(
            (t, f, h, v, pm) for lo, t, f, h, v, pm in _TECH_BY_SIZE
            if pmax >= lo)
        w.generators.append(Generator(
            id=f"gen_{k}", name=ng.plant(tech.value), bus_id=bid[b],
            technology=tech, fuel_id=fuel, prime_mover="thermal",
            p_max_mw=pmax, p_min_pu=pmin, heat_rate_mmbtu_per_mwh=hr,
            vom_per_mwh=vom, fom_per_mw_yr=30_000.0, lifetime_yr=40,
            q_min_mvar=-pmax * 0.4, q_max_mvar=pmax * 0.5,
            mva_base=pmax / 0.9))

    # --- loads with synthesized profiles ---
    for k, ld in net.load.iterrows():
        b = int(ld.bus)
        site = f"load_B{b}_{int(k)}"
        w.loads.append(Load(
            id=f"load_{int(k)}", name=f"{w.buses[0].name} demand",
            bus_id=bid[b], zone_id=zone_of[b],
            demand_profile_id=f"demand__{site}",
            voll_per_mwh=10_000.0))
        w.weather_sites.append(WeatherSite(
            id=site, name=f"load@B{b}", kind="load",
            x=coords[b][0], y=coords[b][1], scale=float(ld.p_mw)))

    # --- fuels / policies / reserves (defaults) ---
    w.fuels = [
        Fuel(id="gas", name="natural gas", price_per_mmbtu=3.5,
             emissions_tco2_per_mmbtu=0.0531),
        Fuel(id="coal", name="coal", price_per_mmbtu=2.0,
             emissions_tco2_per_mmbtu=0.0959),
        Fuel(id="uranium", name="uranium", price_per_mmbtu=0.7,
             emissions_tco2_per_mmbtu=0.0),
    ]
    w.policies = [Policy(id="carbon", kind=PolicyKind.CARBON_PRICE, value=0.0),
                  Policy(id="rps", kind=PolicyKind.RPS, value=0.0)]
    w.reserve_products = [ReserveProduct(
        id="spin", kind=ReserveKind.SPINNING,
        requirement_rule={"pct_load": 0.03})]

    # --- synthesize the weather/demand ensemble ---
    w.weather_model = WeatherModelParams(seed=seed, n_years=n_years,
                                         hours_per_year=8760, load_base_mw=1.0)
    WeatherGenerator(w.weather_model, w.weather_sites).generate(w.time_series_store)

    out.mkdir(parents=True, exist_ok=True)
    save_world(w, out)
    print(f"imported {case}: {len(w.buses)} buses, {len(w.ac_lines)} lines, "
          f"{len(w.generators)} gens, {len(w.loads)} loads, "
          f"{len(zones)} zones -> {out} in {time.time() - t0:.1f}s")
    print("run it:  GLASSBOX_DATA_DIR="
          f"{out} python -m uvicorn glassbox.api.app:app")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("case", help="pandapower.networks case name (case14, case118, ...)")
    ap.add_argument("--out", default=None, help="output dir (default data/<case>)")
    ap.add_argument("--years", type=int, default=3, help="weather years to synthesize")
    a = ap.parse_args()
    import_case(a.case, Path(a.out or f"data/{a.case}"), n_years=a.years)
