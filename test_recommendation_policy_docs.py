from pathlib import Path


POLICY_PATH = Path("RECOMMENDATION_POLICY.md")
INACTIVE_MARKERS = (
    "annotation_zero_weight",
    "weekly_cmo_research.py",
    "weekly_momentum_derivative_research.py",
    "weekly_vbsm_persistence_research.py",
    "weekly_consolidation_persistence_research.py",
    "weekly_consolidation_visualizer.pine",
    "us_signal_formula_parity.py",
    "us_rotation_research.py",
    "us_twin_heikin_ashi_research.py",
    "COMSYN diagnostic case",
    "Institutional accumulation hypothesis",
)


def test_executable_policy_excludes_inactive_research() -> None:
    policy = POLICY_PATH.read_text()

    for marker in INACTIVE_MARKERS:
        assert marker not in policy
