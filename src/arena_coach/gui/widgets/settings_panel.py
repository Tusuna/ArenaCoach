"""Settings panel."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from arena_coach.config import AppConfig
from arena_coach.services.settings_service import SETTINGS_HELP, SettingsService


class SettingsPanel(QWidget):
    config_saved = Signal(object)
    message = Signal(str)
    error = Signal(str)
    export_requested = Signal(dict)
    import_requested = Signal()
    open_folder_requested = Signal(str)
    backup_requested = Signal()

    def __init__(self, service: SettingsService) -> None:
        super().__init__()
        self.service = service
        self.host = QLineEdit()
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.api_path = QLineEdit()
        self.poll_interval = QDoubleSpinBox()
        self.poll_interval.setRange(0.05, 30.0)
        self.poll_interval.setSingleStep(0.05)
        self.timeout = QDoubleSpinBox()
        self.timeout.setRange(0.05, 60.0)
        self.timeout.setSingleStep(0.05)
        self.raw_log_dir = QLineEdit()
        self.database_path = QLineEdit()
        self.database_path.setReadOnly(True)
        self.guided_review = QCheckBox("Use guided match review")
        self.guided_review.setChecked(True)
        self.save_button = QPushButton("Save Settings")
        self.save_button.clicked.connect(self._save)
        self.export_raw_logs = QCheckBox("Include raw logs")
        self.export_debug_logs = QCheckBox("Include debug logs")
        self.export_unfinalized = QCheckBox("Include unfinalized matches")
        self.export_unfinalized.setChecked(True)
        self.export_advanced = QCheckBox("Include advanced analysis")
        self.export_advanced.setChecked(True)
        self.export_button = QPushButton("Export My Data")
        self.import_button = QPushButton("Import Data")
        self.open_exports_button = QPushButton("Open Exports Folder")
        self.open_imports_button = QPushButton("Open Imports Folder")
        self.backup_button = QPushButton("Backup Database Now")
        self.open_backups_button = QPushButton("Open Backups Folder")

        box = QGroupBox("Echo API and Local Paths")
        form = QFormLayout(box)
        general = QLabel(SETTINGS_HELP["general"])
        general.setProperty("class", "muted")
        form.addRow("", general)
        form.addRow("guided review", self.guided_review)
        form.addRow("", _help_label("use_guided_match_review"))
        form.addRow("host", self.host)
        form.addRow("", _help_label("echo_api_host"))
        form.addRow("port", self.port)
        form.addRow("", _help_label("echo_api_port"))
        form.addRow("path", self.api_path)
        form.addRow("", _help_label("echo_api_path"))
        form.addRow("poll interval seconds", self.poll_interval)
        form.addRow("", _help_label("poll_interval_seconds"))
        form.addRow("request timeout seconds", self.timeout)
        form.addRow("", _help_label("request_timeout_seconds"))
        form.addRow("raw log directory", self.raw_log_dir)
        form.addRow("", _help_label("raw_log_dir"))
        form.addRow("database path", self.database_path)
        form.addRow("", _help_label("database_path"))
        form.addRow("", self.save_button)

        data_box = QGroupBox("Data Sharing")
        data_layout = QVBoxLayout(data_box)
        data_help = QLabel(
            "Use exports to send match data back to the developer. Imports are stored separately and do not merge into your main database automatically."
        )
        data_help.setWordWrap(True)
        data_help.setProperty("class", "muted")
        data_layout.addWidget(data_help)
        data_layout.addWidget(self.export_raw_logs)
        data_layout.addWidget(self.export_debug_logs)
        data_layout.addWidget(self.export_unfinalized)
        data_layout.addWidget(self.export_advanced)
        export_buttons = QHBoxLayout()
        export_buttons.addWidget(self.export_button)
        export_buttons.addWidget(self.import_button)
        data_layout.addLayout(export_buttons)
        folder_buttons = QHBoxLayout()
        folder_buttons.addWidget(self.open_exports_button)
        folder_buttons.addWidget(self.open_imports_button)
        data_layout.addLayout(folder_buttons)

        backup_box = QGroupBox("Backups")
        backup_layout = QVBoxLayout(backup_box)
        backup_help = QLabel("Create a backup before risky actions or before updating Arena Coach.")
        backup_help.setWordWrap(True)
        backup_help.setProperty("class", "muted")
        backup_layout.addWidget(backup_help)
        backup_buttons = QHBoxLayout()
        backup_buttons.addWidget(self.backup_button)
        backup_buttons.addWidget(self.open_backups_button)
        backup_layout.addLayout(backup_buttons)

        checklist_box = QGroupBox("First-Run Checklist")
        checklist_layout = QVBoxLayout(checklist_box)
        for line in (
            "1. Create profile",
            "2. Test connection",
            "3. Start logging",
            "4. Stop logging",
            "5. Process match",
            "6. Review and finalize",
            "7. Export data for developer if needed",
        ):
            label = QLabel(line)
            checklist_layout.addWidget(label)

        layout = QVBoxLayout(self)
        layout.addWidget(box)
        layout.addWidget(data_box)
        layout.addWidget(backup_box)
        layout.addWidget(checklist_box)
        layout.addStretch()

        self.export_button.clicked.connect(self._request_export)
        self.import_button.clicked.connect(self.import_requested.emit)
        self.open_exports_button.clicked.connect(lambda: self.open_folder_requested.emit("exports"))
        self.open_imports_button.clicked.connect(lambda: self.open_folder_requested.emit("imports"))
        self.backup_button.clicked.connect(self.backup_requested.emit)
        self.open_backups_button.clicked.connect(lambda: self.open_folder_requested.emit("backups"))
        self.load_values()

    def load_values(self) -> None:
        values = self.service.current_values()
        self.host.setText(values["echo_api_host"])
        self.port.setValue(int(values["echo_api_port"]))
        self.api_path.setText(values["echo_api_path"])
        self.poll_interval.setValue(float(values["poll_interval_seconds"]))
        self.timeout.setValue(float(values["request_timeout_seconds"]))
        self.raw_log_dir.setText(values["raw_log_dir"])
        self.database_path.setText(values["database_path"])
        self.guided_review.setChecked(bool(values.get("use_guided_match_review", True)))

    def _save(self) -> None:
        values = {
            "echo_api_host": self.host.text(),
            "echo_api_port": self.port.value(),
            "echo_api_path": self.api_path.text(),
            "poll_interval_seconds": self.poll_interval.value(),
            "request_timeout_seconds": self.timeout.value(),
            "raw_log_dir": self.raw_log_dir.text(),
            "database_path": self.database_path.text(),
            "use_guided_match_review": self.guided_review.isChecked(),
        }
        try:
            config: AppConfig = self.service.save(values)
        except Exception as exc:
            self.error.emit(str(exc))
            return
        self.message.emit("Settings saved")
        self.config_saved.emit(config)

    def export_options(self) -> dict[str, bool]:
        return {
            "include_raw_logs": self.export_raw_logs.isChecked(),
            "include_debug_logs": self.export_debug_logs.isChecked(),
            "include_unfinalized_matches": self.export_unfinalized.isChecked(),
            "include_advanced_events": self.export_advanced.isChecked(),
        }

    def _request_export(self) -> None:
        self.export_requested.emit(self.export_options())


def _help_label(key: str) -> QLabel:
    label = QLabel(SETTINGS_HELP[key])
    label.setWordWrap(True)
    label.setProperty("class", "muted")
    return label
