"""Dev/test-only correctness oracles (PRD Sections 2.3, 11.1, 11.2).

Mature open-source libraries run *alongside* the transparent kernels to validate
them, never as the production path:

  * pandapower  -> AC power flow + N-1 (Section 6.5)
  * PyPSA       -> economic dispatch / LOPF (Sections 6.2, 6.3)
  * Andes       -> RMS dynamics swing (Section 6.6)

Resource adequacy and EMT have no mature oracle and are validated against
analytical cases instead (binomial-convolution LOLP; LCL resonance, equal-area
CCT, SCR thresholds) — see the engine tests.

Each adapter degrades gracefully if its library is absent (HAVE_* flags), so the
core test suite runs without the heavy oracle dependencies.
"""

from __future__ import annotations


def available() -> dict[str, bool]:
    """Which oracle libraries are importable in this environment."""
    from .andes_oracle import HAVE_ANDES
    from .pandapower_oracle import HAVE_PANDAPOWER
    from .pypsa_oracle import HAVE_PYPSA

    return {"pandapower": HAVE_PANDAPOWER, "pypsa": HAVE_PYPSA, "andes": HAVE_ANDES}


__all__ = ["available"]
