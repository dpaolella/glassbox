"""Parametrized default seed system (PRD Section 8).

The owner ultimately builds and owns the system; the tool ships a default seed
so it runs on first launch. This is a *parametrized construction script*, not a
hardcoded dataset (Section 8): it emits a concrete instance from named,
inspectable parameters meeting the required properties:

  * ~26 buses across 3 zones,
  * a binding inter-zonal interface so nodal and zonal results diverge,
  * a VRE-rich remote zone connected to a load center by limited transmission,
  * synchronous machines (thermal, nuclear, hydro) AND inverter-based resources
    (wind, solar, battery),
  * at least one storage and one hydro unit,
  * dynamic parameters for every generator/converter,
  * a weak, inverter-heavy pocket (low short-circuit ratio) for EMT,
  * candidate generators and one candidate line for CEM.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..schema import (
    ACLine,
    Bus,
    BusType,
    CandidateKind,
    ConverterModel,
    CostCurve,
    CostCurveSegment,
    DCLine,
    Disturbance,
    DisturbanceKind,
    ExpansionCandidate,
    FaultType,
    Fuel,
    Generator,
    GenTechnology,
    Hydro,
    HydroTechnology,
    Interface,
    InterfaceLimitSource,
    Load,
    Policy,
    PolicyKind,
    ReserveKind,
    ReserveProduct,
    ResourcePotential,
    Storage,
    SupplyTranche,
    StorageTechnology,
    SynchronousMachineModel,
    SystemConstraint,
    SystemConstraintKind,
    Transformer,
    WeatherModelParams,
    WeatherSite,
    World,
    Zone,
)
from .namegen import NameGenerator
from ..schema.dynamic_models import (
    ExciterModel,
    GovernorModel,
    InnerCurrentLoop,
    LCLFilter,
    OuterLoop,
    TurbineModel,
)


def _crf(rate: float, lifetime_yr: int) -> float:
    """Capital recovery factor for rough LCOE estimates on candidates."""
    if rate <= 0:
        return 1.0 / lifetime_yr
    f = (1 + rate) ** lifetime_yr
    return rate * f / (f - 1)


@dataclass
class ReferenceSystemParams:
    """Named, inspectable parameters for the default world (Section 8)."""

    name: str = "Glassbox Default System"
    # System per-unit base. This is a multi-GW system, so a 1000 MVA base keeps
    # per-unit flows ~O(1) and the AC power flow well-conditioned (the economic
    # layers work in physical MW and are unaffected by the base).
    base_power_mva: float = 1000.0
    base_frequency_hz: float = 60.0
    n_years: int = 10
    hours_per_year: int = 8760
    seed: int = 42

    # zone sizing
    load_center_buses: int = 9
    renewable_remote_buses: int = 9
    hydro_north_buses: int = 8

    # the binding intertie: deliberately small relative to remote VRE buildout
    intertie_remote_to_center_mva: float = 700.0
    intertie_north_to_center_mva: float = 900.0

    # mean system load (year 0); peak emerges from the shape (~1.8x mean) and
    # is sized to sit just under firm + candidate dispatchable capacity so the
    # default has feasible dispatch headroom with a few scarcity hours.
    mean_load_mw: float = 2200.0
    latitude_deg: float = 41.0


def _sync_machine(mid: str, h: float, technology: str,
                  with_pss: bool = False) -> SynchronousMachineModel:
    """A populated synchronous machine model (GENROU-class params)."""
    gov_droop = 0.05
    return SynchronousMachineModel(
        id=mid,
        h_s=h,
        damping_d=2.0,
        xd=1.8, xq=1.75, xd_t=0.30, xq_t=0.55, xd_st=0.23, xq_st=0.23,
        td0_t=8.0, tq0_t=0.4, td0_st=0.03, tq0_st=0.05,
        governor=GovernorModel(type="TGOV1", droop_r=gov_droop,
                               time_constants={"t1": 0.5, "t2": 1.0, "t3": 5.0},
                               p_min=0.0, p_max=1.0),
        exciter=ExciterModel(type="IEEET1", gains={"ka": 200.0},
                             time_constants={"ta": 0.02, "te": 0.8}, v_ref=1.0,
                             limits={"vrmax": 5.0, "vrmin": -5.0}),
        turbine=TurbineModel(type=technology, time_constants={"tch": 0.3}),
        pss=None,
    )


def _converter(mid: str, mode: str = "grid_following",
               pll_bw: float = 20.0, weak: bool = False) -> ConverterModel:
    """A populated converter model. ``weak`` seeds a fast-PLL grid-following
    unit suitable for the low-SCR EMT micro-example (Section 6.6)."""
    return ConverterModel(
        id=mid,
        control_mode=mode,
        current_limit_pu=1.2,
        pll_bandwidth_hz=(60.0 if weak else pll_bw),
        outer_loop=OuterLoop(p_gain=0.5, q_or_v_gain=0.5,
                             time_constants={"tp": 0.02, "tq": 0.02}),
        inner_current_loop=InnerCurrentLoop(bandwidth_hz=600.0,
                                            gains={"kp": 0.3, "ki": 10.0}),
        droop_p_f=0.05, droop_q_v=0.05,
        virtual_inertia_s=(4.0 if mode == "grid_forming" else 0.0),
        lcl_filter=LCLFilter(l1=0.05, c=0.03, l2=0.05),
        emt_switching_model="averaged",
    )


def _thermal_cost_curve(cid: str, mc_low: float, mc_high: float,
                        pmax: float) -> CostCurve:
    return CostCurve(
        id=cid,
        segments=[
            CostCurveSegment(breakpoint_mw=pmax * 0.5, marginal_cost_per_mwh=mc_low),
            CostCurveSegment(breakpoint_mw=pmax, marginal_cost_per_mwh=mc_high),
        ],
    )


class ReferenceSystemBuilder:
    """Builds the default world from parameters (Section 8 deliverable)."""

    def __init__(self, params: ReferenceSystemParams | None = None):
        self.p = params or ReferenceSystemParams()
        self.world = World(
            id="default",
            name=self.p.name,
            description="Default seed system: 3 zones, binding intertie, mixed "
                        "synchronous + inverter-based resources, weak pocket.",
            base_power_mva=self.p.base_power_mva,
            base_frequency_hz=self.p.base_frequency_hz,
        )
        self._gen_counter = 0

    # --- helpers ---------------------------------------------------------

    def _add_bus(self, bid: str, name: str, zone: str, kv: float,
                 x: float, y: float, bus_type: BusType = BusType.PQ) -> Bus:
        b = Bus(id=bid, name=name, base_kv=kv, zone_id=zone, x=x, y=y, bus_type=bus_type)
        self.world.buses.append(b)
        return b

    def _line(self, fid: str, tid: str, x: float, mva: float, length: float = 80.0) -> ACLine:
        lid = f"L_{fid}_{tid}"
        ln = ACLine(id=lid, name=lid, from_bus_id=fid, to_bus_id=tid,
                    r=x / 10.0, x=x, b=0.02 * length / 80.0, length_km=length,
                    rating_normal_mva=mva, rating_emergency_mva=mva * 1.2,
                    rating_lt_mva=mva * 1.1)
        self.world.ac_lines.append(ln)
        return ln

    # --- build steps -----------------------------------------------------

    def build(self) -> World:
        self._build_zones_and_buses()
        self._build_transmission()
        self._build_generators()
        self._build_hydro()
        self._build_storage()
        self._build_candidates()
        self._build_resource_potentials()
        self._build_loads()
        self._build_fuels_policies_reserves()
        self._build_interfaces_and_constraints()
        self._build_disturbances()
        self._build_weather()
        self._christen()
        self.world.reference_bus_id = "C_slack"
        return self.world

    def _christen(self) -> None:
        """Give everything a real name (issue #26): buses with load become
        cities, other buses substations, plants get named for their city and
        technology. Deterministic from the world seed; ids never change."""
        ng = NameGenerator(self.p.seed)
        w = self.world
        load_buses = {ld.bus_id for ld in w.loads}
        city_of: dict[str, str] = {}
        for b in w.buses:
            if b.id in load_buses:
                b.name = ng.city()
                city_of[b.id] = b.name
            else:
                b.name = ng.substation()
        for g in w.generators:
            g.name = ng.plant(g.technology.value, city_of.get(g.bus_id))
        for h in w.hydro_units:
            h.name = ng.plant(h.technology.value, city_of.get(h.bus_id))
        for st in w.storage_units:
            st.name = ng.plant("battery", city_of.get(st.bus_id))
        for ld in w.loads:
            ld.name = f"{city_of.get(ld.bus_id, ld.bus_id)} demand"
        for c in w.expansion_candidates:
            site = c.bus_id or c.from_bus_id or ""
            if c.kind.value == "line" and c.from_bus_id and c.to_bus_id:
                fa = next((b.name for b in w.buses if b.id == c.from_bus_id), c.from_bus_id)
                ta = next((b.name for b in w.buses if b.id == c.to_bus_id), c.to_bus_id)
                c.name = f"{fa.split()[0]}–{ta.split()[0]} Intertie (proposed)"
            else:
                c.name = ng.plant(c.technology, city_of.get(site)) + " (proposed)"

    def _build_zones_and_buses(self) -> None:
        p = self.p
        # Zone A: load center (urban demand, some firm generation)
        a_buses = []
        for i in range(p.load_center_buses):
            bid = f"A{i+1}"
            bt = BusType.PQ
            a_buses.append(bid)
            self._add_bus(bid, f"LoadCenter-{i+1}", "ZA", 230.0,
                          x=600 + 60 * (i % 3), y=200 + 60 * (i // 3))
        # Zone B: renewable remote (VRE-rich, far, weak pocket at B-tail)
        b_buses = []
        for i in range(p.renewable_remote_buses):
            bid = f"B{i+1}"
            b_buses.append(bid)
            self._add_bus(bid, f"RenewRemote-{i+1}", "ZB", 230.0,
                          x=60 + 60 * (i % 3), y=180 + 60 * (i // 3))
        # Zone C: hydro north (hydro + firm thermal, hosts slack)
        c_buses = []
        for i in range(p.hydro_north_buses):
            bid = f"C{i+1}" if i > 0 else "C_slack"
            c_buses.append(bid)
            bt = BusType.SLACK if i == 0 else BusType.PQ
            self._add_bus(bid, f"HydroNorth-{i+1}", "ZC", 230.0,
                          x=340 + 60 * (i % 3), y=520 + 60 * (i // 3), bus_type=bt)

        self.world.zones = [
            Zone(id="ZA", name="Load Center", member_bus_ids=a_buses),
            Zone(id="ZB", name="Renewable Remote", member_bus_ids=b_buses),
            Zone(id="ZC", name="Hydro North", member_bus_ids=c_buses),
        ]
        self._a_buses, self._b_buses, self._c_buses = a_buses, b_buses, c_buses

    def _build_transmission(self) -> None:
        p = self.p
        # intra-zone meshes (radial-ish chains plus a tie). For zone B the final
        # link (to the tail bus) is omitted so the weak feeder below is the sole
        # connection to the low-SCR pocket.
        for buses in (self._a_buses, self._b_buses, self._c_buses):
            last_link = len(buses) - 1
            if buses is self._b_buses:
                last_link -= 1  # leave b[-1] for the weak feeder only
            for i in range(last_link):
                self._line(buses[i], buses[i + 1], x=0.06, mva=600.0, length=50.0)
            # add a mesh tie to make intra-zonal flow non-trivial
            if len(buses) >= 4:
                self._line(buses[0], buses[2], x=0.08, mva=500.0, length=70.0)

        # weak pocket: the tail of zone B (B last bus) is connected only by a
        # single, high-impedance line -> low short-circuit ratio there.
        weak_bus = self._b_buses[-1]
        # downgrade its single feeder: replace with a thin line
        self._line(self._b_buses[-2], weak_bus, x=0.30, mva=250.0, length=120.0)

        # inter-zonal interties (the binding corridor: remote -> center)
        self._line(self._b_buses[1], self._a_buses[0], x=0.10,
                   mva=p.intertie_remote_to_center_mva, length=220.0)
        self._line(self._b_buses[3], self._a_buses[1], x=0.12,
                   mva=p.intertie_remote_to_center_mva, length=240.0)
        # (a candidate reinforcement line on this corridor is an ExpansionCandidate)
        # north -> center (less constrained)
        self._line(self._c_buses[0], self._a_buses[3], x=0.09,
                   mva=p.intertie_north_to_center_mva, length=180.0)
        self._line(self._c_buses[1], self._a_buses[4], x=0.09,
                   mva=p.intertie_north_to_center_mva, length=190.0)

        # an HVDC link remote->center as an additional controllable corridor
        self.world.dc_lines.append(DCLine(
            id="HVDC_B_A", name="HVDC Remote-Center",
            from_bus_id=self._b_buses[0], to_bus_id=self._a_buses[5],
            p_max_mw=400.0, loss_fraction=0.03,
            dynamic_model_id="cnv_hvdc"))
        self.world.dynamic_models.append(_converter("cnv_hvdc", mode="grid_following"))

        # a transformer at the slack (step-up)
        self.world.transformers.append(Transformer(
            id="T_C_slack", from_bus_id="C_slack", to_bus_id=self._c_buses[1],
            r=0.0, x=0.05, rating_mva=1000.0))

    def _next_gen_id(self, prefix: str) -> str:
        self._gen_counter += 1
        return f"{prefix}{self._gen_counter}"

    def _add_sync_gen(self, bus: str, tech: GenTechnology, pmax: float, h: float,
                      fuel: str, hr: float, vom: float, pmin_pu: float,
                      pss: bool = False,
                      retirement_year: int | None = None) -> Generator:
        """Create an existing synchronous generation asset."""
        gid = self._next_gen_id(tech.value + "_")
        mid = f"sm_{gid}"
        self.world.dynamic_models.append(_sync_machine(mid, h, tech.value, with_pss=pss))
        g = Generator(
            id=gid, name=gid, bus_id=bus, technology=tech, fuel_id=fuel,
            prime_mover="steam" if tech != GenTechnology.NUCLEAR else "nuclear",
            fom_per_mw_yr=30000.0, lifetime_yr=40,
            p_max_mw=pmax, p_min_pu=pmin_pu,
            heat_rate_mmbtu_per_mwh=hr, vom_per_mwh=vom,
            ramp_up_mw_per_min=pmax * 0.01, ramp_down_mw_per_min=pmax * 0.01,
            min_up_time_h=4.0 if tech != GenTechnology.NUCLEAR else 24.0,
            min_down_time_h=4.0, start_cost=pmax * 80.0,
            no_load_cost=pmax * 2.0,
            reserve_eligible=["spin", "nonspin"],
            mttf_h=2000.0, mttr_h=50.0, maintenance_weeks=3.0,
            q_min_mvar=-pmax * 0.4, q_max_mvar=pmax * 0.5,
            v_setpoint_pu=1.0, mva_base=pmax / 0.9,
            retirement_year=retirement_year,
            dynamic_model_id=mid)
        self.world.generators.append(g)
        return g

    def _add_vre_gen(self, bus: str, tech: GenTechnology, pmax: float,
                     site_id: str, weak: bool = False,
                     grid_forming: bool = False) -> Generator:
        """Create an existing inverter-based (VRE) generation asset."""
        gid = self._next_gen_id(tech.value + "_")
        mid = f"cnv_{gid}"
        self.world.dynamic_models.append(
            _converter(mid, mode="grid_forming" if grid_forming else "grid_following",
                       weak=weak))
        g = Generator(
            id=gid, name=gid, bus_id=bus, technology=tech, fuel_id=None,
            prime_mover="inverter", fom_per_mw_yr=24000.0, lifetime_yr=25,
            p_max_mw=pmax, p_min_pu=0.0,
            heat_rate_mmbtu_per_mwh=None, vom_per_mwh=0.5,
            availability_profile_id=f"availability__{site_id}",
            mttf_h=None, mttr_h=None,
            q_min_mvar=-pmax * 0.33, q_max_mvar=pmax * 0.33,
            mva_base=pmax / 0.9,
            dynamic_model_id=mid)
        self.world.generators.append(g)
        return g

    def _build_generators(self) -> None:
        # Zone C (north): nuclear + hydro thermal firm, hosts slack
        self._add_sync_gen("C_slack", GenTechnology.NUCLEAR, 1200.0, h=5.5,
                           fuel="uranium", hr=10.4, vom=2.0, pmin_pu=0.7, pss=True)
        self._add_sync_gen(self._c_buses[2], GenTechnology.CCGT, 450.0, h=4.5,
                           fuel="gas", hr=6.9, vom=3.0, pmin_pu=0.4)
        # the coal unit ages out mid-planning-horizon (issue #33)
        self._add_sync_gen(self._c_buses[3], GenTechnology.COAL, 500.0, h=4.0,
                           fuel="coal", hr=9.5, vom=4.0, pmin_pu=0.4,
                           retirement_year=2034)

        # Zone A (load center): peaking + mid-merit firm capacity near demand
        self._add_sync_gen(self._a_buses[6], GenTechnology.CCGT, 600.0, h=4.5,
                           fuel="gas", hr=6.8, vom=3.0, pmin_pu=0.4)
        self._add_sync_gen(self._a_buses[7], GenTechnology.OCGT, 300.0, h=3.0,
                           fuel="gas", hr=10.5, vom=8.0, pmin_pu=0.2)

        # Zone B (renewable remote): wind + solar; weak pocket has a fast-PLL GFL
        self._add_vre_gen(self._b_buses[0], GenTechnology.WIND, 800.0, "wind_B0")
        self._add_vre_gen(self._b_buses[2], GenTechnology.WIND, 700.0, "wind_B2")
        self._add_vre_gen(self._b_buses[4], GenTechnology.SOLAR_PV, 600.0, "solar_B4")
        self._add_vre_gen(self._b_buses[5], GenTechnology.SOLAR_PV, 500.0, "solar_B5")
        # weak inverter-heavy pocket: grid-following wind on a thin feeder
        self._add_vre_gen(self._b_buses[-1], GenTechnology.WIND, 350.0, "wind_Bweak",
                          weak=True)
        # one grid-forming battery-adjacent solar to contrast GFM vs GFL
        self._add_vre_gen(self._b_buses[7], GenTechnology.SOLAR_PV, 250.0, "solar_B7",
                          grid_forming=True)

    def _build_hydro(self) -> None:
        mid = "sm_hydro1"
        self.world.dynamic_models.append(_sync_machine(mid, h=4.0, technology="hydro"))
        self.world.hydro_units.append(Hydro(
            id="hydro1", name="North Reservoir", bus_id=self._c_buses[4],
            technology=HydroTechnology.RESERVOIR, p_max_mw=500.0, p_min_pu=0.0,
            reservoir_energy_mwh=500.0 * 24 * 7, inflow_profile_id=None,
            mva_base=550.0, dynamic_model_id=mid))

    def _build_storage(self) -> None:
        # battery (converter) in the load center
        self.world.dynamic_models.append(_converter("cnv_batt1", mode="grid_following"))
        self.world.storage_units.append(Storage(
            id="batt1", name="Load Center Battery", bus_id=self._a_buses[2],
            technology=StorageTechnology.BATTERY,
            p_charge_max_mw=200.0, p_discharge_max_mw=200.0, energy_capacity_mwh=800.0,
            efficiency_charge=0.95, efficiency_discharge=0.95,
            soc_min_pu=0.05, soc_max_pu=1.0, vom_per_mwh=1.0,
            fom_per_mw_yr=6_000.0,
            mttf_h=3000.0, mttr_h=24.0, mva_base=200.0,
            dynamic_model_id="cnv_batt1"))

    def _build_candidates(self) -> None:
        """Buildable investment options (the inv layer's Resource Potential)."""
        gas = "gas"

        def lcoe(capex, fom, lifetime, cf, vom=0.0, hr=None, fuel_price=None):
            crf = _crf(0.07, lifetime)
            fixed = (capex * crf + fom) / max(cf * 8760.0, 1.0)
            energy = vom + (hr * fuel_price if hr and fuel_price else 0.0)
            return round(fixed + energy, 1)

        c = self.world.expansion_candidates
        # firm dispatchable in the load center
        c.append(ExpansionCandidate(
            id="cand_ccgt_A9", name="CCGT @ A9", kind=CandidateKind.GENERATOR,
            technology="ccgt", bus_id=self._a_buses[8], zone_id="ZA",
            build_max_mw=800.0, capex_per_mw=1.1e6, fom_per_mw_yr=33_000.0,
            lifetime_yr=30, fuel_id=gas, heat_rate_mmbtu_per_mwh=6.7,
            vom_per_mwh=3.0, p_min_pu=0.4, expected_capacity_factor=0.5,
            lcoe_per_mwh=lcoe(1.1e6, 33_000.0, 30, 0.5, 3.0, 6.7, 3.5)))
        # The bulk remote VRE build-out is modeled zonally as a *supply curve*
        # (see _build_resource_potentials) rather than as two specific plants —
        # that is the "resource potential" view. The candidates here are the
        # specific, sited plants: a firm gas unit, a battery, and the line.
        # candidate storage in the remote zone (independent power/energy sizing)
        c.append(ExpansionCandidate(
            id="cand_batt_B3", name="Battery @ B3", kind=CandidateKind.STORAGE,
            technology="battery", bus_id=self._b_buses[3], zone_id="ZB",
            build_max_mw=400.0, duration_h=4.0, capex_per_mw=240_000.0,
            capex_per_mwh=180_000.0, fom_per_mw_yr=6_000.0, lifetime_yr=15,
            efficiency_charge=0.94, efficiency_discharge=0.94, vom_per_mwh=1.0))
        # Candidate transmission reinforcement on the binding remote->center
        # corridor. Capex is tuned so the nodal CEM finds it economic to relieve
        # the congestion that strands remote solar: the line builds under the
        # nodal view but not the zonal one (which never sees the congestion) —
        # the core "nodal reveals the need for transmission" lesson.
        c.append(ExpansionCandidate(
            id="cand_line_B2_A2", name="Line B2->A2", kind=CandidateKind.LINE,
            technology="line", from_bus_id=self._b_buses[2],
            to_bus_id=self._a_buses[2], zone_id="ZB", build_max_mw=700.0,
            capex_per_mw=150_000.0, lifetime_yr=40, reactance_pu=0.11))

    def _build_resource_potentials(self) -> None:
        """Zonal supply curves — the early-screening 'how much could this region
        host, at what rising cost' view. Best sites are cheapest; CEM builds
        tranches cheapest-first. Distinct from the sited candidates above."""

        def trench_lcoe(capex, fom, cf, vom=0.5, lifetime=25):
            fixed = (capex * _crf(0.07, lifetime) + fom) / max(cf * 8760.0, 1.0)
            return round(fixed + vom, 1)

        rp = self.world.resource_potentials
        # Remote zone (ZB) solar: large, good resource, but interconnects at the
        # corridor head (B2) — so building it pressures the binding intertie.
        rp.append(ResourcePotential(
            id="rp_solar_ZB", name="Solar potential — Remote (ZB)",
            kind=CandidateKind.GENERATOR, technology="solar_pv", zone_id="ZB",
            bus_id=self._b_buses[2], resource_class="solar_pv", vom_per_mwh=0.5,
            fom_per_mw_yr=18_000.0, lifetime_yr=25,
            availability_profile_id="availability__solar_ZB",
            tranches=[
                SupplyTranche(build_max_mw=400.0, capex_per_mw=0.80e6,
                              expected_capacity_factor=0.20,
                              bus_id=self._b_buses[2],
                              availability_profile_id="availability__solar_ZB_t0",
                              lcoe_per_mwh=trench_lcoe(0.80e6, 18_000.0, 0.20)),
                SupplyTranche(build_max_mw=400.0, capex_per_mw=0.95e6,
                              expected_capacity_factor=0.18,
                              bus_id=self._b_buses[5],
                              availability_profile_id="availability__solar_ZB_t1",
                              lcoe_per_mwh=trench_lcoe(0.95e6, 18_000.0, 0.18)),
                SupplyTranche(build_max_mw=400.0, capex_per_mw=1.15e6,
                              expected_capacity_factor=0.16,
                              bus_id=self._b_buses[8],
                              availability_profile_id="availability__solar_ZB_t2",
                              lcoe_per_mwh=trench_lcoe(1.15e6, 18_000.0, 0.16)),
            ]))
        # Remote zone (ZB) wind: best class is cheap per-MWh; tail gets pricey.
        rp.append(ResourcePotential(
            id="rp_wind_ZB", name="Wind potential — Remote (ZB)",
            kind=CandidateKind.GENERATOR, technology="wind", zone_id="ZB",
            bus_id=self._b_buses[2], resource_class="wind", vom_per_mwh=0.5,
            fom_per_mw_yr=26_000.0, lifetime_yr=25,
            availability_profile_id="availability__wind_ZB",
            tranches=[
                SupplyTranche(build_max_mw=400.0, capex_per_mw=1.20e6,
                              expected_capacity_factor=0.36,
                              bus_id=self._b_buses[0],
                              availability_profile_id="availability__wind_ZB_t0",
                              lcoe_per_mwh=trench_lcoe(1.20e6, 26_000.0, 0.36)),
                SupplyTranche(build_max_mw=400.0, capex_per_mw=1.45e6,
                              expected_capacity_factor=0.31,
                              bus_id=self._b_buses[4],
                              availability_profile_id="availability__wind_ZB_t1",
                              lcoe_per_mwh=trench_lcoe(1.45e6, 26_000.0, 0.31)),
                SupplyTranche(build_max_mw=400.0, capex_per_mw=1.75e6,
                              expected_capacity_factor=0.27,
                              bus_id=self._b_buses[7],
                              availability_profile_id="availability__wind_ZB_t2",
                              lcoe_per_mwh=trench_lcoe(1.75e6, 26_000.0, 0.27)),
            ]))
        # Load center (ZA) solar: sited at demand (no corridor needed) but a
        # weaker resource — the locational trade-off vs the remote zone.
        rp.append(ResourcePotential(
            id="rp_solar_ZA", name="Solar potential — Load center (ZA)",
            kind=CandidateKind.GENERATOR, technology="solar_pv", zone_id="ZA",
            bus_id=self._a_buses[4], resource_class="solar_pv", vom_per_mwh=0.5,
            fom_per_mw_yr=18_000.0, lifetime_yr=25,
            availability_profile_id="availability__solar_ZA",
            tranches=[
                SupplyTranche(build_max_mw=300.0, capex_per_mw=1.00e6,
                              expected_capacity_factor=0.16,
                              lcoe_per_mwh=trench_lcoe(1.00e6, 18_000.0, 0.16)),
                SupplyTranche(build_max_mw=300.0, capex_per_mw=1.30e6,
                              expected_capacity_factor=0.15,
                              lcoe_per_mwh=trench_lcoe(1.30e6, 18_000.0, 0.15)),
            ]))

        # Load-center battery potential: a supply curve of storage siting —
        # cheap interconnection first (existing substations), pricier after.
        rp.append(ResourcePotential(
            id="rp_batt_ZA", name="Battery potential — Load center (ZA)",
            kind=CandidateKind.STORAGE, technology="battery", zone_id="ZA",
            bus_id=self._a_buses[5], duration_h=4.0, capex_per_mwh=180_000.0,
            fom_per_mw_yr=6_000.0, lifetime_yr=15,
            efficiency_charge=0.94, efficiency_discharge=0.94, vom_per_mwh=1.0,
            tranches=[
                SupplyTranche(build_max_mw=200.0, capex_per_mw=220_000.0),
                SupplyTranche(build_max_mw=300.0, capex_per_mw=300_000.0),
            ]))

    def _build_loads(self) -> None:
        # demand concentrated in the load center; small loads elsewhere
        peak = self.p.mean_load_mw
        weights = {b: 1.0 for b in self._a_buses}
        # a couple of loads in B and C too
        weights[self._b_buses[3]] = 0.15
        weights[self._c_buses[5]] = 0.2
        total = sum(weights.values())
        for bus, w in weights.items():
            zone = next(z.id for z in self.world.zones if bus in z.member_bus_ids)
            site_id = f"load_{bus}"
            self.world.loads.append(Load(
                id=f"load_{bus}", name=f"Load {bus}", bus_id=bus, zone_id=zone,
                demand_profile_id=f"demand__{site_id}",
                power_factor=0.98, zip_p=0.7, zip_i=0.2, zip_z=0.1,
                motor_fraction=0.3, voll_per_mwh=10_000.0))
            # scale of this load relative to peak captured in weather base
            self._register_load_site(site_id, bus, peak * w / total)

    def _register_load_site(self, site_id: str, bus: str, scale_mw: float) -> None:
        b = self.world.bus(bus)
        # scale carries the per-site peak MW; the weather load shape is ~per-unit
        self.world.weather_sites.append(
            WeatherSite(id=site_id, name=f"load@{bus}", kind="load",
                        x=b.x, y=b.y, scale=scale_mw))

    def _build_fuels_policies_reserves(self) -> None:
        self.world.fuels = [
            Fuel(id="gas", name="Natural Gas", price_per_mmbtu=3.5,
                 emissions_tco2_per_mmbtu=0.053),
            Fuel(id="coal", name="Coal", price_per_mmbtu=2.0,
                 emissions_tco2_per_mmbtu=0.095),
            Fuel(id="uranium", name="Uranium", price_per_mmbtu=0.7,
                 emissions_tco2_per_mmbtu=0.0),
        ]
        # policies present but inert by default (toggled in scenarios, Section 10)
        self.world.policies = [
            Policy(id="carbon", kind=PolicyKind.CARBON_PRICE, value=0.0),
            Policy(id="rps", kind=PolicyKind.RPS, value=0.35),
            Policy(id="prm", kind=PolicyKind.PLANNING_RESERVE_MARGIN, value=0.15),
        ]
        self.world.reserve_products = [
            ReserveProduct(id="spin", kind=ReserveKind.SPINNING,
                           requirement_rule={"pct_load": 0.03, "pct_vre": 0.05}),
            ReserveProduct(id="nonspin", kind=ReserveKind.NON_SPINNING,
                           requirement_rule={"pct_load": 0.03}),
            ReserveProduct(id="ffr", kind=ReserveKind.FAST_FREQUENCY_RESPONSE,
                           requirement_rule={"fixed_mw": 0.0}),
        ]

    def _build_interfaces_and_constraints(self) -> None:
        # the binding remote->center interface (the nodal-vs-zonal lesson)
        corridor = [ln.id for ln in self.world.ac_lines
                    if ln.from_bus_id in self._b_buses and ln.to_bus_id in self._a_buses]
        self.world.interfaces.append(Interface(
            id="IF_remote_center", name="Remote->Center Intertie",
            member_line_ids=corridor,
            direction_weights=[1.0] * len(corridor),
            limit_mw=self.p.intertie_remote_to_center_mva,
            limit_source=InterfaceLimitSource.THERMAL))
        # min-inertia / RoCoF system constraints (inert until dynamics feeds them)
        self.world.system_constraints = [
            SystemConstraint(id="min_inertia", kind=SystemConstraintKind.MIN_INERTIA,
                             value=0.0),
            SystemConstraint(id="rocof", kind=SystemConstraintKind.ROCOF_LIMIT,
                             value=1.0),
        ]

    def _build_disturbances(self) -> None:
        # N-1 outages of each intertie line (pf), plus a three-phase fault (dyn/emt)
        for ln in self.world.ac_lines:
            if ln.from_bus_id in self._b_buses and ln.to_bus_id in self._a_buses:
                self.world.disturbances.append(Disturbance(
                    id=f"N1_{ln.id}", name=f"Outage {ln.id}",
                    affected_element_ids=[ln.id], kind=DisturbanceKind.ELEMENT_OUTAGE))
        self.world.disturbances.append(Disturbance(
            id="fault_center", name="3ph fault at load center",
            affected_element_ids=[self._a_buses[0]], kind=DisturbanceKind.BUS_FAULT,
            fault_type=FaultType.THREE_PHASE, fault_impedance_ohm=0.01,
            apply_time_s=1.0, clear_time_s=1.08))

    def _build_weather(self) -> None:
        # VRE sites bound to availability profile ids referenced by existing
        # generators AND by candidate VRE (so candidate profiles are generated).
        def add_site(profile_id, bus_id, tech, quality=1.0):
            site_id = profile_id.split("__", 1)[1]
            b = self.world.bus(bus_id)
            kind = "wind" if "wind" in tech else "solar"
            self.world.weather_sites.append(
                WeatherSite(id=site_id, name=f"{kind}@{bus_id}", kind=kind,
                            x=b.x, y=b.y, scale=quality))

        for g in self.world.generators:
            if g.availability_profile_id:
                add_site(g.availability_profile_id, g.bus_id, g.technology.value)
        for cand in self.world.expansion_candidates:
            if cand.availability_profile_id and cand.bus_id:
                add_site(cand.availability_profile_id, cand.bus_id, cand.technology)
        # zonal resource-potential VRE supply curves need their own profiles
        # Zonal supply curves carry a resource-quality scale: the remote zone
        # (ZB) hosts the good sites (that is the whole locational trade-off),
        # the load center (ZA) the weaker ones (issue #10 partial).
        rp_quality = {"rp_solar_ZB": 1.30, "rp_wind_ZB": 1.10, "rp_solar_ZA": 1.0}
        for rp in self.world.resource_potentials:
            hub = rp.bus_id or (self.world.zone(rp.zone_id).member_bus_ids[0]
                                if rp.zone_id else None)
            q = rp_quality.get(rp.id, 1.0)
            if rp.availability_profile_id and hub:
                add_site(rp.availability_profile_id, hub, rp.technology, q)
            # each tranche is a distinct site: its own bus, and quality that
            # declines with the CF metadata (best sites first, physically)
            cf0 = (rp.tranches[0].expected_capacity_factor
                   if rp.tranches and rp.tranches[0].expected_capacity_factor else None)
            for tr in rp.tranches:
                bus = tr.bus_id or hub
                if tr.availability_profile_id and bus:
                    tq = q
                    if cf0 and tr.expected_capacity_factor:
                        tq = q * tr.expected_capacity_factor / cf0
                    add_site(tr.availability_profile_id, bus, rp.technology, tq)

        # load shape is per-unit (~1.0 mean); each load site's peak MW is carried
        # by WeatherSite.scale, applied inside the generator.
        self.world.weather_model = WeatherModelParams(
            seed=self.p.seed, n_years=self.p.n_years,
            hours_per_year=self.p.hours_per_year, latitude_deg=self.p.latitude_deg,
            load_base_mw=1.0)


def build_default_world() -> World:
    """Build the default seed world (no weather time series yet)."""
    return ReferenceSystemBuilder().build()
