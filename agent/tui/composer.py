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
        if app is not None and getattr(app, "_composer_blocked", lambda: False)():
            event.stop()
            event.prevent_default()
            return

        if app is not None and getattr(app, "_approval_pending", lambda: False)():
            if await self._handle_approval_key(event):
                return
            if event.character or event.key not in ("escape",):
                event.stop()
                event.prevent_default()
            return

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
                if hasattr(self.app, "_sync_mention_popup"):
                    self.app._sync_mention_popup()  # type: ignore[attr-defined]
                return

        if app is not None and getattr(app, "_mention_candidates", None):
            if event.key == "down":
                app._mention_highlight = min(  # type: ignore[attr-defined]
                    len(app._mention_candidates) - 1,
                    app._mention_highlight + 1,  # type: ignore[attr-defined]
                )
                app._refresh_mention_highlight()  # type: ignore[attr-defined]
                event.stop()
                event.prevent_default()
                return
            if event.key == "up":
                app._mention_highlight = max(0, app._mention_highlight - 1)  # type: ignore[attr-defined]
                app._refresh_mention_highlight()  # type: ignore[attr-defined]
                event.stop()
                event.prevent_default()
                return

        if event.character and len(event.character) == 1:
            pasted = self._paste_burst.feed(event.character)
            if pasted:
                event.stop()
                event.prevent_default()
                self.insert(pasted)
                return

        await super()._on_key(event)

    async def _handle_approval_key(self, event: events.Key) -> bool:
        """Single-key approval (y/n/a/A) without Enter; Enter still accepted."""
        app = self._app_composer()
        if app is None:
            return False

        key = event.character or ""
        if event.key == "enter":
            text = self.text.strip()
            if text:
                app._submit_input(text)  # type: ignore[attr-defined]
                event.stop()
                event.prevent_default()
                return True
            return False

        if key in ("y", "Y", "n", "N"):
            app._submit_input(key.lower())  # type: ignore[attr-defined]
            event.stop()
            event.prevent_default()
            return True
        if key == "a":
            app._submit_input("a")  # type: ignore[attr-defined]
            event.stop()
            event.prevent_default()
            return True
        if key == "A":
            app._submit_input("A")  # type: ignore[attr-defined]
            event.stop()
            event.prevent_default()
            return True
        return False

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
        if hasattr(app, "_restore_bindings_for_text"):
            app._restore_bindings_for_text(self.text)  # type: ignore[attr-defined]
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
