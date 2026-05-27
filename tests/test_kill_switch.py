from __future__ import annotations

from pathlib import Path

from olibuguard.kill_switch import KillSwitch


def _ks(tmp_path: Path) -> KillSwitch:
    return KillSwitch(tmp_path / "KILL_SWITCH")


def test_inactive_by_default(tmp_path: Path) -> None:
    assert not _ks(tmp_path).is_active()


def test_activate_makes_active(tmp_path: Path) -> None:
    ks = _ks(tmp_path)
    ks.activate()
    assert ks.is_active()


def test_deactivate_makes_inactive(tmp_path: Path) -> None:
    ks = _ks(tmp_path)
    ks.activate()
    ks.deactivate()
    assert not ks.is_active()


def test_activate_is_idempotent(tmp_path: Path) -> None:
    ks = _ks(tmp_path)
    ks.activate()
    ks.activate()  # must not raise
    assert ks.is_active()


def test_deactivate_is_noop_when_inactive(tmp_path: Path) -> None:
    ks = _ks(tmp_path)
    ks.deactivate()  # must not raise
    assert not ks.is_active()


def test_sentinel_file_is_human_readable(tmp_path: Path) -> None:
    ks = _ks(tmp_path)
    ks.activate(reason="test run")
    content = ks.path.read_text()
    assert "kill_switch: active" in content
    assert "activated_at:" in content
    assert "reason: test run" in content


def test_activate_creates_parent_dirs(tmp_path: Path) -> None:
    ks = KillSwitch(tmp_path / "nested" / "deep" / "KILL_SWITCH")
    ks.activate()
    assert ks.is_active()


def test_path_property_matches_constructor(tmp_path: Path) -> None:
    p = tmp_path / "KILL_SWITCH"
    assert KillSwitch(p).path == p
