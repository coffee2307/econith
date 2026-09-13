import pytest

from KHKT_Evaluation.common.vol_eval import _compare


def score(mae, rmse):
    return {"mae": mae, "rmse": rmse, "directional_accuracy": 0.5}


@pytest.mark.parametrize("candidate,expected", [(score(1, 1), True), (score(1, 3), False), (score(2, 2), False), (score(None, 1), None)])
def test_h1_requires_both_metrics_and_both_controls(candidate, expected):
    result = _compare(score(2, 2), candidate, score(2, 2))
    assert result["h1_point_estimate_supported"] is expected


def test_h1_must_also_beat_shuffled_control():
    result = _compare(score(3, 3), score(2, 2), score(1, 1))
    assert result["h1_point_estimate_supported"] is False
