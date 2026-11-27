# SPDX-License-Identifier: GPL-3.0-or-later

from PyQt6.QtCore import QAbstractAnimation, QEasingCurve, QEvent, QPropertyAnimation, QRectF, QSize, Qt, pyqtProperty, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QAbstractButton, QVBoxLayout
from qfluentwidgets import (
    CaptionLabel, FluentIconBase, TextBrowser, Theme, ThemeColor, isDarkTheme, qconfig, setFont,
)

from UI import ui_icons as icons
from UI import ui_layout_settings as ls
from UI.i18n import LocalizedPage, tr


UPDATE_STATES = frozenset(("idle", "checking", "available", "downloading", "ready", "current", "error"))


class UpdateActionButton(QAbstractButton):
    """One fixed-size action with a vector spinner, progress ring or check mark."""

    def __init__(self, parent=None):
        super().__init__(parent)
        setFont(self, 14)
        self.setFixedSize(ls.COMPACT_ACTION_MIN_WIDTH, ls.CONTROL_HEIGHT)
        self.setIconSize(QSize(16, 16))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._state = "idle"
        self._received = 0
        self._total = 0
        self._rotation = 0.0
        self._animation = QPropertyAnimation(self, b"rotation", self)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(360.0)
        self._animation.setDuration(1000)
        self._animation.setLoopCount(-1)
        self._animation.setEasingCurve(QEasingCurve.Type.Linear)
        self._fluent_icon = None
        # Follow the theme colour (the Windows accent) and light/dark switches like other primary buttons.
        qconfig.themeChanged.connect(self._theme_changed)
        qconfig.themeColorChanged.connect(self._theme_changed)

    @pyqtProperty(float)
    def rotation(self) -> float:
        return self._rotation

    @rotation.setter
    def rotation(self, angle: float) -> None:
        self._rotation = angle
        self.update()

    @property
    def progress(self) -> float | None:
        return min(self._received / self._total, 1.0) if self._total > 0 else None

    @property
    def animation_running(self) -> bool:
        return self._animation.state() == QAbstractAnimation.State.Running

    def set_visual_state(self, state: str) -> None:
        self._state = state
        self._sync_animation()
        self.update()

    def set_progress(self, received: int, total: int) -> None:
        self._received, self._total = max(received, 0), max(total, 0)
        self._sync_animation()
        self.update()

    def _sync_animation(self) -> None:
        spinning = self._state == "checking" or (self._state == "downloading" and self._total == 0)
        state = self._animation.state()
        if not spinning:
            self._animation.stop()
        elif not self.isVisible():
            if state == QAbstractAnimation.State.Running:
                self._animation.pause()
        elif state == QAbstractAnimation.State.Paused:
            self._animation.resume()
        elif state == QAbstractAnimation.State.Stopped:
            self._animation.start()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_animation()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        if self.animation_running:
            self._animation.pause()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.update()

    def set_fluent_icon(self, icon: FluentIconBase | None) -> None:
        self._fluent_icon = icon
        self._sync_icon()

    def _sync_icon(self) -> None:
        icon = self._fluent_icon
        self.setIcon(icon.icon(Theme.LIGHT, color=self.foreground_color()) if icon else QIcon())

    def _theme_changed(self, *_) -> None:
        self._sync_icon()
        self.update()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange:
            self._sync_icon()

    def foreground_color(self) -> QColor:
        """Text and symbol colour of a Fluent primary button on the current theme colour."""
        return QColor("#000000" if isDarkTheme() and self.isEnabled() else "#ffffff")

    def _background_color(self) -> QColor:
        dark = isDarkTheme()
        if not self.isEnabled():
            return QColor(52, 52, 52) if dark else QColor(205, 205, 205)
        if self.isDown():
            return (ThemeColor.DARK_2 if dark else ThemeColor.LIGHT_3).color()
        if self.underMouse():
            return (ThemeColor.DARK_1 if dark else ThemeColor.LIGHT_1).color()
        return ThemeColor.PRIMARY.color()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._background_color())
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.drawRoundedRect(rect, 5, 5)
        if not self.isEnabled():
            painter.setOpacity(0.43 if isDarkTheme() else 0.9)
        elif self.isDown():
            painter.setOpacity(0.63)
        foreground = self.foreground_color()
        pen = QPen(foreground, 2.1, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        center = rect.center()
        ring = QRectF(center.x() - 8, center.y() - 8, 16, 16)
        if self._state == "checking" or (self._state == "downloading" and self.progress is None):
            painter.drawArc(ring, int((90 - self._rotation) * 16), -110 * 16)
        elif self._state == "downloading":
            track = QColor(foreground)
            track.setAlpha(50)
            painter.setPen(QPen(track, 2.1))
            painter.drawEllipse(ring)
            painter.setPen(pen)
            painter.drawArc(ring, 90 * 16, -round(360 * 16 * self.progress))
        elif self._state == "current":
            check = QPainterPath()
            check.moveTo(center.x() - 7, center.y())
            check.lineTo(center.x() - 2, center.y() + 5)
            check.lineTo(center.x() + 7, center.y() - 5)
            painter.drawPath(check)
        else:
            painter.setPen(foreground)
            painter.setFont(self.font())
            text_rect = rect
            if not self.icon().isNull():
                icon_width = self.iconSize().width()
                text_width = self.fontMetrics().horizontalAdvance(self.text())
                left = center.x() - (icon_width + ls.ACTION_ICON_SPACING + text_width) / 2
                icon_rect = QRectF(left, center.y() - icon_width / 2, icon_width, icon_width)
                self.icon().paint(painter, icon_rect.toRect())
                text_rect = QRectF(left + icon_width + ls.ACTION_ICON_SPACING, rect.top(), text_width, rect.height())
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, self.text())


class UpdatePanel(LocalizedPage):
    """Keep the action in the version row; only release notes expand the card."""

    check_requested = pyqtSignal()
    download_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    install_requested = pyqtSignal()
    width_hint_changed = pyqtSignal()

    def __init__(self, parent=None, version: str = ""):
        super().__init__()
        self.setParent(parent)
        self._state = "idle"
        self._version = ""
        self._fallback_version = version
        self._preferred_width = 0
        self._message = ""
        self._error_source = "idle"
        self._install_allowed = False
        self._received = 0
        self._total = 0
        # The button belongs to the card, so hiding the notes does not hide the action.
        self.action_button = UpdateActionButton(parent)
        self.action_button.clicked.connect(self._request_action)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(ls.PAGE_SPACING)
        self.notes_label = self.localized(CaptionLabel(self), "Release Notes")
        self.notes_label.setTextColor(light=ls.SECONDARY_TEXT_LIGHT, dark=ls.SECONDARY_TEXT_DARK)
        layout.addWidget(self.notes_label)
        self.notes_view = TextBrowser(self)
        self.notes_view.setReadOnly(True)
        self.notes_view.setOpenLinks(False)
        self.notes_view.setOpenExternalLinks(False)
        self.notes_view.setFixedHeight(96)
        self.notes_view.document().setMaximumBlockCount(1000)
        layout.addWidget(self.notes_view)
        self.set_state("idle")

    def set_state(self, state: str, version: str = "", notes: str = "", message: str = "") -> None:
        if state not in UPDATE_STATES:
            raise ValueError(f"Unknown update state: {state}")
        if state == "error" and self._state != "error":
            self._error_source = self._state
        if state == "downloading" and self._state != state:
            self._received = self._total = 0
            self.action_button.set_progress(0, 0)
        self._state, self._version, self._message = state, version, message
        self.action_button.set_visual_state(state)
        self.notes_view.setPlainText(notes)
        self.setVisible(bool(notes) and state in ("available", "downloading", "ready"))
        self._refresh_text()

    def set_progress(self, received: int, total: int) -> None:
        self._received, self._total = max(received, 0), max(total, 0)
        self.action_button.set_progress(received, total)
        self._refresh_text()

    def set_install_allowed(self, allowed: bool) -> None:
        self._install_allowed = allowed
        self._refresh_text()

    def preferred_action_width(self) -> int:
        version = self._version or self._fallback_version
        texts = (
            tr("Check for Updates"), tr("Download {version}").format(version=version),
            tr("Install {version}").format(version=version), tr("Check failed"),
            tr("Download failed"), tr("Update failed"),
        )
        return ls.action_button_width(self.action_button, texts, icon_width=self.action_button.iconSize().width())

    def _request_action(self) -> None:
        if self._state in ("checking", "downloading"):
            self.cancel_requested.emit()
        elif self._state == "available":
            self.download_requested.emit()
        elif self._state == "ready":
            if self._install_allowed:
                self.install_requested.emit()
        else:
            self.check_requested.emit()

    def _refresh_text(self) -> None:
        state = self._state
        text = ""
        icon = None
        name = tr("Check for Updates")
        tip = name
        if state == "checking":
            name = tr("Cancel update check")
            tip = tr("Checking for updates. Click to cancel.")
        elif state == "downloading":
            name = tr("Cancel update download")
            if self._total > 0:
                percent = min(round(self._received * 100 / self._total), 100)
                tip = tr("Downloading update {version}: {percent}%. Click to cancel.").format(
                    version=self._version, percent=percent,
                )
            else:
                tip = tr("Downloading update {version}. Click to cancel.").format(version=self._version)
        elif state == "current":
            tip = tr("You are using the latest version. Click to check again.")
        elif state == "available":
            text = tr("Download {version}").format(version=self._version)
            icon = icons.UPDATE_DOWNLOAD
            name = tip = text
        elif state == "ready":
            text = tr("Install {version}").format(version=self._version)
            icon = icons.UPDATE_INSTALL
            name = text
            tip = tr(self._message) if self._message else tr("Install the update and restart the application")
            if not self._install_allowed and not self._message:
                tip = tr("Update installation is currently unavailable")
        elif state == "error":
            icon = icons.UPDATE_FAILED
            if self._error_source == "downloading":
                text = tr("Download failed")
            elif self._error_source == "ready":
                text = tr("Update failed")
            else:
                text = tr("Check failed")
            name = tr("Retry update check")
            details = tr(self._message) if self._message else tr("Update failed")
            tip = details + "\n" + tr("Click to retry")
        else:
            text = name
            icon = icons.UPDATE_CHECK
        self.action_button.set_fluent_icon(icon)
        self.action_button.setText(text)
        self.action_button.setAccessibleName(name)
        self.action_button.setAccessibleDescription(tip)
        self.action_button.setToolTip(tip)
        self.action_button.setEnabled(state != "ready" or self._install_allowed)
        self.action_button.update()
        width = self.preferred_action_width()
        if width != self._preferred_width:
            self._preferred_width = width
            self.width_hint_changed.emit()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self._refresh_text()
