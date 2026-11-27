# Qt Package Tool - A PyQt6-based application
# Copyright (c) 2025 NCFXZ
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QBoxLayout, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CaptionLabel, FluentIconBase, IconWidget, SimpleCardWidget, SmoothScrollArea


PAGE_CONTENT_WIDTH = 960
PAGE_MARGIN = 15
PAGE_SPACING = 8
SECTION_SPACING = 10
CARD_INSET = 18
CARD_VERTICAL_INSET = 14
FORM_CARD_HEIGHT = 70
FORM_ICON_SIZE = 20
ICON_TEXT_SPACING = 20
CONTROL_SPACING = 10
CONTROL_HEIGHT = 32
FORM_FIELD_WIDTH = 320
ACTION_WIDTH = 154
COMPACT_ACTION_MIN_WIDTH = 128
ACTION_ICON_SPACING = 8
ACTION_HORIZONTAL_PADDING = 24
TABLE_HEIGHT = 200
TABLE_HEADER_HEIGHT = 42
TABLE_CELL_PADDING = 24
SECONDARY_TEXT_LIGHT = "#5d5d5d"
SECONDARY_TEXT_DARK = "#cfcfcf"
# Muted warm taupe, with separate tones for readable light and dark cards.
CAUTION_TEXT_LIGHT = "#78695c"
CAUTION_TEXT_DARK = "#b8aa9c"
DEFAULT_TEXT_LIGHT = "#000000"
DEFAULT_TEXT_DARK = "#ffffff"


class ElidedLabel(BodyLabel):
    """
    Body text that shortens in the middle to fit its width and shows the full text as a tooltip.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._full_text = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def setText(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(text)
        self._elide()

    def text(self) -> str:
        return self._full_text

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        metrics = self.fontMetrics()
        super().setText(metrics.elidedText(self._full_text, Qt.TextElideMode.ElideMiddle, max(self.width(), 0)))


def page_content_layout(page: QWidget) -> QVBoxLayout:
    scroll = SmoothScrollArea(page)
    scroll.setWidgetResizable(True)
    scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
    scroll.enableTransparentBackground()
    content = QWidget()
    content.setMaximumWidth(PAGE_CONTENT_WIDTH)
    scroll.setWidget(content)
    content.setAutoFillBackground(False)
    layout = QVBoxLayout(content)
    layout.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
    layout.setSpacing(PAGE_SPACING)
    QVBoxLayout(page).addWidget(scroll)
    return layout


def set_card_padding(layout: QBoxLayout) -> None:
    layout.setContentsMargins(CARD_INSET, CARD_VERTICAL_INSET, CARD_INSET, CARD_VERTICAL_INSET)
    layout.setSpacing(CONTROL_SPACING)


def form_row(
    icon: FluentIconBase, title: QWidget, *controls: QWidget, caption: CaptionLabel | None = None
) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(0)
    icon_widget = IconWidget(icon)
    icon_widget.setFixedSize(FORM_ICON_SIZE, FORM_ICON_SIZE)
    row.addWidget(icon_widget, 0, Qt.AlignmentFlag.AlignVCenter)
    row.addSpacing(ICON_TEXT_SPACING)

    if caption is None:
        row.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)
    else:
        caption.setTextColor(light=SECONDARY_TEXT_LIGHT, dark=SECONDARY_TEXT_DARK)
        text = QWidget()
        labels = QVBoxLayout(text)
        labels.setSpacing(0)
        labels.setContentsMargins(0, 0, 0, 0)
        labels.addWidget(title)
        labels.addWidget(caption)
        row.addWidget(text, 0, Qt.AlignmentFlag.AlignVCenter)
    row.addStretch(1)
    for control in controls:
        row.addSpacing(CONTROL_SPACING)
        row.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
    return row


def form_card(row: QHBoxLayout) -> SimpleCardWidget:
    card = SimpleCardWidget()
    card.setFixedHeight(FORM_CARD_HEIGHT)
    row.setContentsMargins(CARD_INSET, 0, CARD_INSET, 0)
    card.setLayout(row)
    return card


def centered_actions(*buttons: QWidget) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(CONTROL_SPACING)
    row.addStretch(1)
    for button in buttons:
        row.addWidget(button)
    row.addStretch(1)
    return row


def action_button_width(button, texts=None, *, icon_width: int | None = None) -> int:
    """Fit translated text and an icon with compact, consistent horizontal padding."""
    if texts is None:
        texts = (button.text(),)
    if icon_width is None:
        icon_width = 0 if button.icon().isNull() else button.iconSize().width()
    content = max((button.fontMetrics().horizontalAdvance(text) for text in texts), default=0)
    if icon_width:
        content += icon_width + ACTION_ICON_SPACING
    return max(COMPACT_ACTION_MIN_WIDTH, content + ACTION_HORIZONTAL_PADDING, button.minimumSizeHint().width())
