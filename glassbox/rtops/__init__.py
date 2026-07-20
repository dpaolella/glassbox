"""Real-time operations (control room) machinery — PRD issue #56.

Phase 0a: the substation layer. ``elaborate`` grows a node-breaker substation
model out of any bus-branch world; ``topology`` collapses it back (the EMS's
topology processing); ``switching`` operates devices under interlocks.
"""

from .elaborate import elaborate_world
from .kernel import OpsSimulation, ShiftConfig, ShiftResult, run_shift
from .session import OpsSession
from .switching import SwitchResult, operate_switch, reset_switches
from .topology import DerivedTopology, derive_bus_branch

__all__ = ["elaborate_world", "OpsSimulation", "ShiftConfig", "ShiftResult", "run_shift", "OpsSession", "derive_bus_branch", "DerivedTopology",
           "operate_switch", "reset_switches", "SwitchResult"]
