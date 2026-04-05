"""
Главное Textual-приложение COD-DOC.
Точка входа: cod-doc tui
"""

from __future__ import annotations

from pathlib import Path
from textual.app import App, ComposeResult
from textual.binding import Binding

from cod_doc.config import Config
from cod_doc.tui.screens.dashboard import DashboardScreen
from cod_doc.tui.screens.wizard import WizardScreen


class CodDocApp(App):
    """COD-DOC TUI."""

    TITLE = "COD-DOC — Context Orchestrator for Documentation"
    CSS_PATH = str(Path(__file__).parent / "cod_doc.tcss")

    BINDINGS = [
        Binding("q", "quit", "Выход", show=True),
        Binding("ctrl+c", "quit", "Выход", show=False),
    ]

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config

    def on_mount(self) -> None:
        if not self.config.is_configured:
            self.push_screen(WizardScreen(self.config))
        else:
            self.push_screen(DashboardScreen(self.config))
