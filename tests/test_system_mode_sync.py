"""SystemController and /mode must stay in lockstep (no Quant/UI desync)."""
from __future__ import annotations

from core.mode import QuantMode, get_mode_manager
from core.system_controller import OperatingMode, get_system_controller


def test_mode_route_syncs_system_controller_and_quant() -> None:
    """POST /mode semantics: SystemController.set_mode drives ModeManager."""
    ctrl = get_system_controller()
    mgr = get_mode_manager()

    ctrl.set_mode(OperatingMode.AUTONOMOUS_HYPOTHESIS)
    assert mgr.mode is QuantMode.SIMULATION
    assert ctrl.operating_mode is OperatingMode.AUTONOMOUS_HYPOTHESIS

    # Legacy QuantControls still posts REALITY|SIMULATION to /mode — must reset
    # operating_mode, not leave it stuck on AUTONOMOUS_HYPOTHESIS.
    state = ctrl.set_mode("REALITY")
    assert state.operating_mode == "REALITY"
    assert state.quant_mode == "REALITY"
    assert mgr.mode is QuantMode.REALITY
    assert ctrl.operating_mode is OperatingMode.REALITY

    state = ctrl.set_mode("SIMULATION")
    assert state.quant_mode == "SIMULATION"
    assert mgr.mode is QuantMode.SIMULATION
    assert ctrl.snapshot().operating_mode == "SIMULATION"
    assert ctrl.snapshot().autonomous_hypothesis_implemented is True
