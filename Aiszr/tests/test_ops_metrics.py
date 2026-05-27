from ops_metrics import OpsMetrics


def test_metrics_snapshot_contains_loss_and_qps():
    m = OpsMetrics(qps_window_sec=10)
    m.record_input(now=100.0)
    m.record_reply(now=100.0)
    m.observe_latency_ms(120)
    m.inc_loss("decode_fail", 2)
    snap = m.snapshot()
    assert "input_qps" in snap
    assert "reply_qps" in snap
    assert snap["loss"]["decode_fail"] == 2
    assert snap["p95_latency_ms"] >= 0
