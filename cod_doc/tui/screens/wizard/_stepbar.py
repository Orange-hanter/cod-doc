"""Horizontal step indicator widget used at the top of the wizard."""

from __future__ import annotations

from textual.widgets import Static

from ._styles import STEPBAR_CSS


class _StepBar(Static):
    """Горизонтальный индикатор шагов."""

    DEFAULT_CSS = STEPBAR_CSS

    def __init__(self, steps: list[str], current: int = 0) -> None:
        super().__init__("")
        self._steps = steps
        self._current = current
        self._refresh_label()

    def update_step(self, current: int) -> None:
        self._current = current
        self._refresh_label()

    def _refresh_label(self) -> None:
        parts: list[str] = []
        for i, name in enumerate(self._steps):
            if i < self._current:
                parts.append(f"[dim]✓ {name}[/dim]")
            elif i == self._current:
                parts.append(f"[bold $accent]● {name}[/bold $accent]")
            else:
                parts.append(f"[dim]○ {name}[/dim]")
            if i < len(self._steps) - 1:
                parts.append("[dim] → [/dim]")
        self.update("".join(parts))
