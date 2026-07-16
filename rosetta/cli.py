"""rosetta CLI.

    rosetta translate case14 --from matpower --to glassbox --hub pypsa -o out/
    rosetta compare-hubs case14 --from matpower --to glassbox --hubs pypsa,sienna
    rosetta roundtrip data/default_world --schema glassbox --hub pypsa
    rosetta bridges
"""

from __future__ import annotations

import argparse
import json
import sys

from .core import merged_manifest, translate
from .schemas import dump, load


def _print_manifest(m: dict) -> None:
    print(f"route: {' | '.join(m['route'])}")
    t = m["totals"]
    print(f"  approximated={t['approximated']}  parked={t['parked']}  "
          f"restored={t['restored']}  dropped={t['dropped']}  "
          f"invented={t['invented']}  manual-mapping={t['manual_mapping_required']}  "
          f"sidecar-remaining={m['sidecar_remaining']}")


def cmd_translate(a) -> int:
    p = load(a.src_schema, a.source)
    p = translate(p, a.to, hub=a.hub, opts={"hours": a.hours})
    out = dump(p, a.out)
    _print_manifest(merged_manifest(p))
    print(f"wrote {out} (model + sidecar.json + coverage.json)")
    return 0


def cmd_compare_hubs(a) -> int:
    rows = []
    for hub in a.hubs.split(","):
        hub = hub.strip()
        p = load(a.src_schema, a.source)
        try:
            p = translate(p, a.to, hub=hub, opts={"hours": a.hours})
        except Exception as exc:
            rows.append((hub, None, str(exc)))
            continue
        rows.append((hub, merged_manifest(p), None))
        if a.out:
            dump(p, f"{a.out}/via_{hub}")
    print(f"\n{a.src_schema}:{a.source} -> {a.to}, one row per hub\n")
    hdr = f"{'hub':10} {'approx':>7} {'parked':>7} {'restored':>9} " \
          f"{'dropped':>8} {'invented':>9} {'manual-map':>11} {'in-sidecar':>11}"
    print(hdr)
    print("-" * len(hdr))
    for hub, m, err in rows:
        if err:
            print(f"{hub:10} FAILED: {err}")
            continue
        t = m["totals"]
        print(f"{hub:10} {t['approximated']:>7} {t['parked']:>7} "
              f"{t['restored']:>9} {t['dropped']:>8} {t['invented']:>9} "
              f"{t['manual_mapping_required']:>11} {m['sidecar_remaining']:>11}")
    print("\nNumbers are events, not judgements: a parked concept that a later "
          "leg restores is safer than\na silent approximation. Read the full "
          "hop-by-hop story in coverage.json (--out to write it).")
    return 0


def cmd_roundtrip(a) -> int:
    p = load(a.schema, a.source)
    p = translate(p, a.schema, hub=a.hub, opts={"hours": a.hours})
    m = merged_manifest(p)
    _print_manifest(m)
    if a.out:
        dump(p, a.out)
        print(f"wrote {a.out}")
    if a.json:
        print(json.dumps(m, indent=2))
    return 0


def cmd_bridges(_a) -> int:
    from .core import bridges as reg
    for (src, dst), b in sorted(reg().items()):
        print(f"{src:10} -> {dst:10}  {b.notes}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rosetta",
                                 description="hub-and-spoke schema translation "
                                             "test bench")
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("translate", help="translate a model, optionally via a hub")
    t.add_argument("source")
    t.add_argument("--from", dest="src_schema", required=True)
    t.add_argument("--to", required=True)
    t.add_argument("--hub", default=None)
    t.add_argument("--hours", type=int, default=168)
    t.add_argument("-o", "--out", default="out")
    t.set_defaults(fn=cmd_translate)

    c = sub.add_parser("compare-hubs", help="same task through each hub; diff manifests")
    c.add_argument("source")
    c.add_argument("--from", dest="src_schema", required=True)
    c.add_argument("--to", required=True)
    c.add_argument("--hubs", required=True, help="comma-separated hub schemas")
    c.add_argument("--hours", type=int, default=168)
    c.add_argument("-o", "--out", default=None)
    c.set_defaults(fn=cmd_compare_hubs)

    r = sub.add_parser("roundtrip", help="X -> hub -> X; what survives?")
    r.add_argument("source")
    r.add_argument("--schema", required=True)
    r.add_argument("--hub", required=True)
    r.add_argument("--hours", type=int, default=168)
    r.add_argument("-o", "--out", default=None)
    r.add_argument("--json", action="store_true")
    r.set_defaults(fn=cmd_roundtrip)

    b = sub.add_parser("bridges", help="list registered bridges")
    b.set_defaults(fn=cmd_bridges)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
