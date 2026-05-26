"""Composer input: Enter submits, Shift+Enter newline, paste burst, history."""

from __future__ import annotations

from textual import events
from textual.widgets import TextArea

from agent.tui.paste_burst import PasteBurstDetector


class ComposerTextArea(TextArea):
    """TextArea with Codex-inspired input ergonomics."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._paste_burst = PasteBurstDetector()
        self._history_index: int | None = None
        self._history_browse: list[str] = []

    def _app_composer(self):
        app = self.app
        if not hasattr(app, "_input_history"):
            return None
        return app

    async def _on_key(self, event: events.Key) -> None:
        if self.read_only:
            return

        app = self._app_composer()

        if app is not None and getattr(app, "_reverse_search_active", False):
            await self._handle_reverse_search_key(event)
            return

        if event.key == "enter":
            event.stop()
            event.prevent_default()
            if hasattr(self.app, "_submit_input"):
                self.app._submit_input(self.text)  # type: ignore[attr-defined]
            return

        if event.key == "shift+enter":
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return

        if event.key == "ctrl+r" and app is not None:
            event.stop()
            event.prevent_default()
            app.action_reverse_search()  # type: ignore[attr-defined]
            return

        if event.key == "up" and app is not None:
            if self._history_navigate(app, delta=-1):
                event.stop()
                event.prevent_default()
                return

        if event.key == "down" and app is not None:
            if self._history_navigate(app, delta=1):
                event.stop()
                event.prevent_default()
                return

        if event.key == "tab" and hasattr(self.app, "_expand_file_mention"):
            expanded = self.app._expand_file_mention(self.text)  # type: ignore[attr-defined]
            if expanded is not None:
                event.stop()
                event.prevent_default()
                self.text = expanded
                return

        if event.character and len(event.character) == 1:
            pasted = self._paste_burst.feed(event.character)
            if pasted:
                event.stop()
                event.prevent_default()
                self.insert(pasted)
                return

        await super()._on_key(event)

    def _history_navigate(self, app, *, delta: int) -> bool:
        entries = app._input_history.all_entries()  # type: ignore[attr-defined]
        if not entries:
            return False
        if self._history_index is None:
            self._history_browse = list(entries)
            self._history_index = len(self._history_browse) if delta < 0 else -1
        self._history_index = max(
            -1, min(len(self._history_browse) - 1, self._history_index + delta)
        )
        if self._history_index < 0:
            self.text = ""
            self._history_index = None
            return True
        self.text = self._history_browse[self._history_index]
        return True

    def reset_history_navigation(self) -> None:
        self._history_index = None
        self._history_browse = []

    async def _handle_reverse_search_key(self, event: events.Key) -> None:
        app = self.app
        if event.key == "escape":
            event.stop()
            event.prevent_default()
            app.action_reverse_search_cancel()  # type: ignore[attr-defined]
            return
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            app.action_reverse_search_accept()  # type: ignore[attr-defined]
            return
        if event.key == "backspace":
            event.stop()
            event.prevent_default()
            app._reverse_search_query = app._reverse_search_query[:-1]  # type: ignore[attr-defined]
            app._update_reverse_search()  # type: ignore[attr-defined]
            return
        if event.character and event.character.isprintable():
            event.stop()
            event.prevent_default()
            app._reverse_search_query += event.character  # type: ignore[attr-defined]
            app._update_reverse_search()  # type: ignore[attr-defined]
