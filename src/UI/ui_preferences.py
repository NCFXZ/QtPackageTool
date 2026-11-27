# SPDX-License-Identifier: GPL-3.0-or-later

from PyQt6.QtCore import QSignalBlocker
from qfluentwidgets import BodyLabel, CaptionLabel, ComboBox, StrongBodyLabel, SwitchButton, Theme, TitleLabel

from app.preferences import AUTO_JOBS, CPU_COUNT, JOB_CHOICES, AppPreferences
from UI import ui_layout_settings as ls
from UI import ui_icons as icons
from UI.i18n import LocalizedPage


THEMES = {"system": Theme.AUTO, "light": Theme.LIGHT, "dark": Theme.DARK}


class PreferencesUI(LocalizedPage):
    def __init__(self, preferences: AppPreferences):
        super().__init__()
        self.setObjectName("PreferencesUI")
        layout = ls.page_content_layout(self)

        layout.addWidget(self.localized(TitleLabel(self), "Settings"))
        layout.addSpacing(ls.PAGE_MARGIN)
        layout.addWidget(self.localized(StrongBodyLabel(self), "Language"))

        self.language_combo = ComboBox()
        self.language_combo.setFixedSize(ls.ACTION_WIDTH, ls.CONTROL_HEIGHT)
        self.language_combo.addItem("简体中文", userData="zh_CN")
        self.language_combo.addItem("English", userData="en")
        self.language_combo.setCurrentIndex(self.language_combo.findData(preferences.language))
        self.language_combo.currentIndexChanged.connect(
            lambda _: preferences.select_language(self.language_combo.currentData())
        )
        preferences.language_changed.connect(self._sync_language)
        self._preferences = preferences
        layout.addWidget(ls.form_card(ls.form_row(
            icons.LANGUAGE, self.localized(BodyLabel(self), "Display Language"), self.language_combo
        )))

        layout.addSpacing(ls.SECTION_SPACING)
        layout.addWidget(self.localized(StrongBodyLabel(self), "Appearance"))
        self.theme_combo = ComboBox()
        self.theme_combo.setFixedSize(ls.ACTION_WIDTH, ls.CONTROL_HEIGHT)
        for index, (theme, label) in enumerate(
            (("system", "Follow System"), ("light", "Light"), ("dark", "Dark"))
        ):
            self.theme_combo.addItem(label, userData=theme)
            self.bind_text(lambda text, i=index: self.theme_combo.setItemText(i, text), label)
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(preferences.theme))
        self.theme_combo.currentIndexChanged.connect(
            lambda _: preferences.select_theme(self.theme_combo.currentData())
        )
        layout.addWidget(ls.form_card(ls.form_row(
            icons.THEME, self.localized(BodyLabel(self), "App Theme"), self.theme_combo
        )))

        layout.addSpacing(ls.SECTION_SPACING)
        layout.addWidget(self.localized(StrongBodyLabel(self), "Build"))
        self.jobs_combo = ComboBox(self)
        self.jobs_combo.setFixedSize(ls.ACTION_WIDTH, ls.CONTROL_HEIGHT)
        for jobs in JOB_CHOICES:
            self.jobs_combo.addItem(str(jobs), userData=jobs)
        self.bind_text(lambda text: self.jobs_combo.setItemText(0, text), "Automatic")
        self.jobs_combo.setCurrentIndex(self.jobs_combo.findData(preferences.jobs))
        self.jobs_combo.currentIndexChanged.connect(
            lambda _: preferences.select_jobs(self.jobs_combo.currentData())
        )
        layout.addWidget(ls.form_card(ls.form_row(
            icons.PARALLEL_JOBS, self.localized(BodyLabel(self), "Parallel Jobs"), self.jobs_combo,
            caption=self.localized(CaptionLabel(self), "Compile commands that run at the same time"),
        )))

        layout.addSpacing(ls.SECTION_SPACING)
        layout.addWidget(self.localized(StrongBodyLabel(self), "Updates"))
        self.auto_download_updates_switch = SwitchButton(self)
        self.auto_download_updates_switch.setChecked(preferences.auto_download_updates)
        self.auto_download_updates_switch.checkedChanged.connect(preferences.select_auto_download_updates)
        preferences.auto_download_updates_changed.connect(self._sync_auto_download_updates)
        self.bind_text(self.auto_download_updates_switch.setOnText, "On")
        self.bind_text(self.auto_download_updates_switch.setOffText, "Off")
        layout.addWidget(ls.form_card(ls.form_row(
            icons.AUTO_DOWNLOAD_UPDATES, self.localized(BodyLabel(self), "Automatically Download Updates"),
            self.auto_download_updates_switch,
            caption=self.localized(CaptionLabel(self), "Download new versions found at startup"),
        )))
        layout.addStretch(1)

    def _sync_language(self) -> None:
        with QSignalBlocker(self.language_combo):
            self.language_combo.setCurrentIndex(self.language_combo.findData(self._preferences.language))

    def _sync_auto_download_updates(self, enabled: bool) -> None:
        with QSignalBlocker(self.auto_download_updates_switch):
            self.auto_download_updates_switch.setChecked(enabled)
