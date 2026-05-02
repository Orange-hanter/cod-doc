"""COD-026: TUI smoke tests — app boot routing.

Covers:
- Wizard appears when the API key isn't configured.
- Dashboard appears when the API key is configured.

Tests stay headless via `App.run_test(...)`; no fixtures touch the user's
real config (~/.cod-doc) — every test passes its own `Config` instance.
"""

from __future__ import annotations

import pytest

from cod_doc.config import Config
from cod_doc.tui.app import CodDocApp
from cod_doc.tui.screens.dashboard import DashboardScreen
from cod_doc.tui.screens.wizard import WizardScreen


def _make_config(*, configured: bool, tmp_path) -> Config:  # type: ignore[no-untyped-def]
    """Build a Config with an isolated config directory."""
    cfg = Config(
        api_key="sk-test-1234567890" if configured else "",
        cod_doc_home=str(tmp_path / ".cod-doc"),
    )
    return cfg


@pytest.mark.asyncio
async def test_app_boots_into_wizard_when_unconfigured(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """No API key → wizard screen is the active one after on_mount."""
    cfg = _make_config(configured=False, tmp_path=tmp_path)
    app = CodDocApp(cfg)
    async with app.run_test() as pilot:
        await pilot.pause()  # let on_mount + push_screen settle
        assert isinstance(app.screen, WizardScreen)


@pytest.mark.asyncio
async def test_app_boots_into_dashboard_when_configured(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """API key present → dashboard screen is the active one."""
    cfg = _make_config(configured=True, tmp_path=tmp_path)
    app = CodDocApp(cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)


@pytest.mark.asyncio
async def test_app_quits_on_q_binding(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Pressing 'q' from any screen triggers App.action_quit."""
    cfg = _make_config(configured=True, tmp_path=tmp_path)
    app = CodDocApp(cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        # If quit fired, the app's exit flag is set; pilot context exits cleanly.
        assert app._exit is True
