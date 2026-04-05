"""
Главное Textual-приложение COD-DOC.
Точка входа: cod-doc tui
"""

from __future__ import annotations

import logging
from pathlib import Path
from textual.app import App, ComposeResult
from textual.binding import Binding

from cod_doc.config import Config
from cod_doc.logging_config import get_logger
from cod_doc.tui.screens.dashboard import DashboardScreen
from cod_doc.tui.screens.wizard import WizardScreen


log = get_logger("tui.app")


class CodDocApp(App):
    """COD-DOC TUI."""

    TITLE = "COD-DOC — Context Orchestrator for Documentation"
    CSS_PATH = str(Path(__file__).parent / "cod_doc.tcss")

    BINDINGS = [
        Binding("q", "quit", "Выход", show=True),
        Binding("ctrl+c", "quit", "Выход", show=False),
    ]

    def __init__(self, config: Config, debug_log_file: str | None = None) -> None:
        super().__init__()
        self.config = config
        self.debug_log_file = debug_log_file
        self._configure_tui_debug_logger()

    def _configure_tui_debug_logger(self) -> None:
        if not self.debug_log_file:
            return
        path = Path(self.debug_log_file).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)

        logger = logging.getLogger("cod_doc.tui")
        logger.setLevel(logging.DEBUG)

        # Не добавлять дубликаты обработчиков для одного и того же файла.
        for handler in logger.handlers:
            if isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) == path:
                return

        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        logger.addHandler(file_handler)
        logger.propagate = True
        log.debug("TUI debug logging enabled", extra={"tool": str(path)})

    def on_mount(self) -> None:
        log.debug("CodDocApp mounted", extra={"event_type": "app_mount"})
        if not self.config.is_configured:
            log.debug("No API key configured, opening wizard", extra={"event_type": "open_wizard"})
            self.push_screen(WizardScreen(self.config))
        else:
            log.debug("API key configured, opening dashboard", extra={"event_type": "open_dashboard"})
            self.push_screen(DashboardScreen(self.config))
