"""Profile management panel."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from arena_coach.services.profile_service import ProfileService


class ProfilePanel(QWidget):
    data_changed = Signal()
    message = Signal(str)
    error = Signal(str)

    def __init__(self, service: ProfileService) -> None:
        super().__init__()
        self.service = service
        self.profile_combo = QComboBox()
        self.active_label = QLabel("none")
        self.create_display = QLineEdit()
        self.create_echo = QLineEdit()
        self.edit_display = QLineEdit()
        self.edit_echo = QLineEdit()
        self.create_button = QPushButton("Create Profile")
        self.set_active_button = QPushButton("Set Active")
        self.save_active_button = QPushButton("Save Active Profile")

        self.create_button.clicked.connect(self._create_profile)
        self.set_active_button.clicked.connect(self._set_active)
        self.save_active_button.clicked.connect(self._save_active)
        self.profile_combo.currentIndexChanged.connect(self._load_selected_into_edit)

        active_box = QGroupBox("Active Profile")
        active_layout = QFormLayout(active_box)
        active_layout.addRow("active", self.active_label)
        active_layout.addRow("profiles", self.profile_combo)
        active_layout.addRow("", self.set_active_button)

        create_box = QGroupBox("Create Profile")
        create_layout = QFormLayout(create_box)
        create_layout.addRow("display name", self.create_display)
        create_layout.addRow("Echo name", self.create_echo)
        create_layout.addRow("", self.create_button)

        edit_box = QGroupBox("Edit Active Profile")
        edit_layout = QFormLayout(edit_box)
        edit_layout.addRow("display name", self.edit_display)
        edit_layout.addRow("Echo name", self.edit_echo)
        edit_layout.addRow("", self.save_active_button)

        layout = QVBoxLayout(self)
        layout.addWidget(active_box)
        row = QHBoxLayout()
        row.addWidget(create_box)
        row.addWidget(edit_box)
        layout.addLayout(row)
        layout.addStretch()
        self.reload()

    def reload(self) -> None:
        profiles = self.service.list_profiles()
        active = next((profile for profile in profiles if profile["active"]), None)
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for profile in profiles:
            label = f"#{profile['id']} {profile['display_name']}"
            if profile["primary_echo_name"]:
                label += f" ({profile['primary_echo_name']})"
            self.profile_combo.addItem(label, profile["id"])
            if profile["active"]:
                self.profile_combo.setCurrentIndex(self.profile_combo.count() - 1)
        self.profile_combo.blockSignals(False)
        self.active_label.setText(
            f"#{active['id']} {active['display_name']} ({active['primary_echo_name'] or 'no Echo name'})"
            if active
            else "none"
        )
        if active:
            self.edit_display.setText(active["display_name"])
            self.edit_echo.setText(active["primary_echo_name"] or "")

    def _selected_profile_id(self) -> int | None:
        value = self.profile_combo.currentData()
        return int(value) if value is not None else None

    def _load_selected_into_edit(self) -> None:
        profile_id = self._selected_profile_id()
        if profile_id is None:
            return
        profile = next((item for item in self.service.list_profiles() if item["id"] == profile_id), None)
        if profile:
            self.edit_display.setText(profile["display_name"])
            self.edit_echo.setText(profile["primary_echo_name"] or "")

    def _create_profile(self) -> None:
        try:
            profile_id = self.service.create_profile(self.create_display.text(), self.create_echo.text())
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.message.emit(f"Created profile #{profile_id}")
        self.create_display.clear()
        self.create_echo.clear()
        self.reload()
        self.data_changed.emit()

    def _set_active(self) -> None:
        profile_id = self._selected_profile_id()
        if profile_id is None:
            self.error.emit("Select a profile first.")
            return
        try:
            self.service.set_active_profile(profile_id)
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.message.emit(f"Active profile set to #{profile_id}")
        self.reload()
        self.data_changed.emit()

    def _save_active(self) -> None:
        try:
            self.service.update_active_profile(self.edit_display.text(), self.edit_echo.text())
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.message.emit("Active profile saved")
        self.reload()
        self.data_changed.emit()
