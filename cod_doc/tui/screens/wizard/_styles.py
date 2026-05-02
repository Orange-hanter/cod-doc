"""Textual CSS for the wizard screen — extracted as a string constant."""

from __future__ import annotations

WIZARD_CSS = """
WizardScreen {
    align: center middle;
}

#wizard-frame {
    width: 80;
    height: auto;
    max-height: 90vh;
    border: double $primary;
    background: $surface;
    padding: 0;
}

#wizard-title-bar {
    background: $primary;
    color: $text;
    text-style: bold;
    padding: 1 2;
    height: 3;
    content-align: center middle;
}

#wizard-content {
    padding: 2 4;
    height: auto;
}

.step-heading {
    text-style: bold;
    color: $accent;
    margin-bottom: 1;
}

.field-label {
    color: $text-muted;
    margin-top: 1;
}

.hint {
    color: $text-muted;
    text-style: italic;
    padding: 0 0 1 0;
}

.error-label {
    color: $error;
    text-style: bold;
    height: 1;
}

#wizard-nav {
    height: 5;
    align: center middle;
    padding: 1 2;
    border-top: solid $surface-darken-2;
}

#wizard-nav Button { margin: 0 1; min-width: 18; }

RadioSet { height: auto; margin: 1 0; }
RadioButton { height: 1; }

#welcome-art {
    color: $accent;
    text-style: bold;
    content-align: center middle;
    height: 5;
}

#welcome-body { margin: 1 0; }

#done-art {
    color: $success;
    text-style: bold;
    content-align: center middle;
    height: 3;
}
"""

STEPBAR_CSS = """
_StepBar {
    height: 3;
    content-align: center middle;
    background: $surface-darken-1;
    padding: 0 2;
}
"""
