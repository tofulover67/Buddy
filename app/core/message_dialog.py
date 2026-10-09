#!/usr/bin/env python3
"""
alert(), confirm() and prompt(): a title, some text, one or two buttons -
and for prompt() a line to type in - drawn like the rest of Buddy
(app/web/shell/message/) instead of a Qt message box. Modal over the
window you pass.

Protocol:
    to the view    message
    from the view  answer
"""

import os

from PySide6.QtWidgets import QDialog

from core.web_page import WEB_COMMON_DIR, WebDialog, _theme_host


class MessageDialog(WebDialog):
    web_dir = os.path.join(WEB_COMMON_DIR, "shell", "message")

    def __init__(self, host, parent, title, text, ok="OK", cancel=None, danger=False, field=None, size=None):
        self._message = {"title": title, "text": text, "ok": ok, "cancel": cancel, "danger": bool(danger),
                         "field": field}
        self.answer = False
        self.typed = ""
        super().__init__(host, parent, title, size or (440, 250 if field is not None else 220))

    def web_ready(self):
        self.emit("message", self._message)

    def on_answer(self, payload):
        self.answer = bool((payload or {}).get("ok"))
        typed = (payload or {}).get("text")
        self.typed = typed[:500] if isinstance(typed, str) else ""
        self.done(QDialog.Accepted if self.answer else QDialog.Rejected)


def alert(parent, title, text):
    """A message over `parent`."""
    dialog = MessageDialog(_theme_host(parent), parent, title, text)
    dialog.exec()
    dialog.deleteLater()        # a child of its parent: freed now, not with the window


def confirm(parent, title, text, ok="OK", cancel="Cancel", danger=False, size=None):
    """True if they pressed `ok`. `size` (width, height) for a longer text."""
    dialog = MessageDialog(_theme_host(parent), parent, title, text, ok, cancel, danger, size=size)
    dialog.exec()
    answer = dialog.answer
    dialog.deleteLater()
    return answer


def prompt(parent, title, text, value="", ok="OK", cancel="Cancel", placeholder=""):
    """What they typed, or None if they cancelled."""
    dialog = MessageDialog(_theme_host(parent), parent, title, text, ok, cancel,
                           field={"value": value, "placeholder": placeholder})
    dialog.exec()
    typed = dialog.typed if dialog.answer else None
    dialog.deleteLater()
    return typed
