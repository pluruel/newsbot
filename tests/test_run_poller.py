from unittest.mock import patch

import pytest

from newsparser.collector import run_poller


def test_market_pulse_sends_each_alert_as_plain_text():
    """Pulse messages carry verbatim headlines, so they must not go out through
    the HTML sender — one `<` or `&` in a title would sink the whole message."""
    with patch.object(run_poller.pulse, "check", return_value=["a", "b"]), \
         patch.object(run_poller, "_send_plain") as plain, \
         patch.object(run_poller, "_send") as html:
        run_poller._market_pulse()
    assert [c.args[0] for c in plain.call_args_list] == ["a", "b"]
    html.assert_not_called()


def test_market_pulse_send_failure_does_not_propagate():
    with patch.object(run_poller.pulse, "check", return_value=["a"]), \
         patch.object(run_poller, "send_long_message", side_effect=RuntimeError("net")):
        run_poller._market_pulse()  # must not raise


def test_market_pulse_default_enabled():
    assert run_poller.MARKET_PULSE_ENABLED is True


def test_alert_send_uses_no_parse_mode():
    """Breaking/spike/pulse messages all embed verbatim scraped titles; sent as
    HTML, one `<` in a headline (e.g. '<속보>') gets the whole message rejected
    with 400 Can't parse entities — and the try/except swallows the loss."""
    with patch.object(run_poller, "send_message") as sm:
        run_poller._send("⚡ Breaking\n<속보> 금리 인하")
    sm.assert_called_once_with("⚡ Breaking\n<속보> 금리 인하", parse_mode=None)


def test_baseline_alpha_tracks_poll_interval():
    """The EMA decays per tick, so a fixed α would tie the baseline's memory to
    the poll cadence: halving the interval halved the wall-clock half-life and
    let the baseline absorb ramps detect_spike used to flag. α is derived so
    the half-life stays BASELINE_HALFLIFE_S at any cadence — at the original
    600s it lands on the measured α=0.3."""
    assert 1 - 0.5 ** (600 / run_poller.BASELINE_HALFLIFE_S) == pytest.approx(0.3, abs=0.01)
    decay_per_halflife = (1 - run_poller.BASELINE_ALPHA) ** (
        run_poller.BASELINE_HALFLIFE_S / run_poller.POLL_INTERVAL)
    assert decay_per_halflife == pytest.approx(0.5)


def test_poll_interval_default_is_300s(monkeypatch):
    """15m bars need headlines in the DB by the time a bar closes; 600s left too
    much of the window unobserved.

    load_dotenv is stubbed out because the repo's own .env pins the variable —
    which is also the reason changing this default is not enough on a deployed
    host: the operator has to drop POLL_INTERVAL_SECONDS from .env (or set it to
    300) for the new value to take effect.
    """
    import importlib
    monkeypatch.delenv("POLL_INTERVAL_SECONDS", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    reloaded = importlib.reload(run_poller)
    try:
        assert reloaded.POLL_INTERVAL == 300
    finally:
        monkeypatch.undo()
        importlib.reload(run_poller)
