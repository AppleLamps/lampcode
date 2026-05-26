"""TUI composer submit behavior (Enter key, focus)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from textual.widgets import TextArea

from agent.tui.app import AgentTuiApp

@pytest.mark.asyncio
async def test_enter_in_composer_submits_when_textarea_has_focus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.chdir(tmp_path)

    submitted: list[str] = []

    app = AgentTuiApp(cwd=tmp_path)
    async with app.run_test() as pilot:
        textarea = app.query_one("#input", TextArea)
        await pilot.click(textarea)
        await pilot.pause()
        assert textarea.has_focus

        await pilot.press("h", "i")
        with patch.object(app, "_submit_input", submitted.append):
            await pilot.press("enter")
        await pilot.pause()

    assert submitted == ["hi"]
