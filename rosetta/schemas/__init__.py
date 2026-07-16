"""Schema load/dump registry.

Each schema the bench knows is registered here with a loader (path or
built-in name -> native object) and a dumper (native object -> directory).
The native in-memory forms are each schema's OWN objects — pandapower nets,
PyPSA Networks, Glassbox Worlds, SiennaSystem models — never a rosetta type.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ..core import Payload

_LOADERS: dict[str, Callable[[str], Any]] = {}
_DUMPERS: dict[str, Callable[[Any, Path], None]] = {}


def register(name: str, loader, dumper) -> None:
    _LOADERS[name] = loader
    _DUMPERS[name] = dumper


def load(schema: str, source: str) -> Payload:
    if schema not in _LOADERS:
        raise KeyError(f"unknown schema '{schema}'; known: {sorted(_LOADERS)}")
    return Payload(schema=schema, native=_LOADERS[schema](source))


def dump(payload: Payload, out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    _DUMPERS[payload.schema](payload.native, out)
    # the sidecar and manifest always ride along with the dumped model
    (out / "sidecar.json").write_text(json.dumps(payload.sidecar.to_json(), indent=2))
    from ..core import merged_manifest
    (out / "coverage.json").write_text(json.dumps(merged_manifest(payload), indent=2))
    return out


# --- matpower / IEEE cases (via pandapower) ---------------------------------


def _load_matpower(source: str):
    import pandapower as pp
    import pandapower.networks as pn

    if hasattr(pn, source):                     # "case14", "case30", "case118", …
        return getattr(pn, source)()
    if source.endswith(".json"):
        return pp.from_json(source)
    raise ValueError(f"matpower source '{source}' is neither a built-in "
                     "pandapower case name nor a pandapower .json file")


def _dump_matpower(net, out: Path) -> None:
    import pandapower as pp
    pp.to_json(net, str(out / "network.json"))


register("matpower", _load_matpower, _dump_matpower)


# --- PyPSA -------------------------------------------------------------------


def _load_pypsa(source: str):
    import pypsa
    n = pypsa.Network()
    n.import_from_netcdf(source)
    return n


def _dump_pypsa(n, out: Path) -> None:
    n.export_to_netcdf(str(out / "network.nc"))


register("pypsa", _load_pypsa, _dump_pypsa)


# --- Glassbox ------------------------------------------------------------------


def _load_glassbox(source: str):
    from glassbox.world import load_world
    return load_world(source)


def _dump_glassbox(world, out: Path) -> None:
    from glassbox.world import save_world
    save_world(world, out)


register("glassbox", _load_glassbox, _dump_glassbox)


# --- Sienna (schema mirror) ----------------------------------------------------


def _load_sienna(source: str):
    from .sienna import SiennaSystem
    return SiennaSystem.model_validate_json(Path(source).read_text())


def _dump_sienna(system, out: Path) -> None:
    (out / "system.json").write_text(system.model_dump_json(indent=2))


register("sienna", _load_sienna, _dump_sienna)
