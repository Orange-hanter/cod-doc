"""COD-026: TUI smoke tests — individual screens compose without exceptions.

Each test mounts a single screen via `App.run_test(...)` and verifies the
expected widgets exist. No business logic, no LLM calls, no DB writes —
just routing + Button.Pressed handlers don't crash.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import pytest
from textual.app import App, ComposeResult

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project
from cod_doc.tui.screens.agent_run import AgentRunScreen
from cod_doc.tui.screens.dashboard import (
    AddProjectDialog,
    AddTaskDialog,
    DashboardScreen,
)
from cod_doc.tui.screens.wizard import WizardScreen

if TYPE_CHECKING:
    from pathlib import Path

    from textual.screen import Screen


class _ScreenHost(App[Any]):
    """Minimal host App that shows one screen — used to mount/test screens."""

    SCREENS: ClassVar[dict[str, type[Screen[Any]]]] = {}

    def __init__(self, screen_factory) -> None:  # type: ignore[no-untyped-def]
        super().__init__()
        self._screen_factory = screen_factory

    def compose(self) -> ComposeResult:
        return iter([])

    def on_mount(self) -> None:
        self.push_screen(self._screen_factory())


def _make_config(tmp_path: Path, *, configured: bool = True) -> Config:
    return Config(
        api_key="sk-test-1234567890" if configured else "",
        cod_doc_home=str(tmp_path / ".cod-doc"),
    )


def _make_project(tmp_path: Path) -> Project:
    repo = tmp_path / "repo"
    repo.mkdir()
    entry = ProjectEntry(name="proj", path=str(repo), master_md="MASTER.md")
    proj = Project(entry)
    proj.init()
    return proj


# ============================================================================ #
# WizardScreen                                                                  #
# ============================================================================ #


@pytest.mark.asyncio
async def test_wizard_screen_mounts(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path, configured=False)
    app = _ScreenHost(lambda: WizardScreen(cfg))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, WizardScreen)


# ============================================================================ #
# DashboardScreen                                                               #
# ============================================================================ #


@pytest.mark.asyncio
async def test_dashboard_screen_mounts_with_no_projects(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    app = _ScreenHost(lambda: DashboardScreen(cfg))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)


@pytest.mark.asyncio
async def test_dashboard_refresh_button_does_not_crash(tmp_path: Path) -> None:
    """Pressing Refresh on an empty dashboard does not raise."""
    cfg = _make_config(tmp_path)
    app = _ScreenHost(lambda: DashboardScreen(cfg))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("r")  # action_refresh binding
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)


# ============================================================================ #
# Dialogs (modals over Dashboard)                                                #
# ============================================================================ #


@pytest.mark.asyncio
async def test_add_project_dialog_mounts(tmp_path: Path) -> None:
    cfg = _make_config(tmp_path)
    app = _ScreenHost(lambda: AddProjectDialog(cfg))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, AddProjectDialog)


@pytest.mark.asyncio
async def test_add_task_dialog_mounts(tmp_path: Path) -> None:
    proj = _make_project(tmp_path)
    app = _ScreenHost(lambda: AddTaskDialog(proj))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, AddTaskDialog)


# ============================================================================ #
# AgentRunScreen                                                                #
# ============================================================================ #


@pytest.mark.asyncio
async def test_agent_run_screen_mounts(tmp_path: Path) -> None:
    """AgentRunScreen composes without an LLM call (start button is disabled)."""
    cfg = _make_config(tmp_path)
    proj = _make_project(tmp_path)
    app = _ScreenHost(lambda: AgentRunScreen(proj, cfg))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, AgentRunScreen)
        # Footer is rendered
        from textual.widgets import Footer

        assert app.screen.query(Footer)
