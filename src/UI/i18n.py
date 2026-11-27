# SPDX-License-Identifier: GPL-3.0-or-later

from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QWidget

from app.localization import load_translators, preferred_language, tr


class LocalizedPage(QWidget):
    def __init__(self):
        super().__init__()
        self._localized = []

    def bind_text(self, setter, source: str) -> None:
        self._localized.append((setter, source))
        setter(tr(source))

    def localized(self, widget, source: str):
        self.bind_text(widget.setText, source)
        return widget

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            for setter, source in self._localized:
                setter(tr(source))
