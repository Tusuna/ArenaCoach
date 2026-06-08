"""Small QRunnable helpers for non-blocking GUI tasks."""

from __future__ import annotations

from typing import Any, Callable
import traceback

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class FunctionWorker(QRunnable):
    def __init__(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.function(*self.args, **self.kwargs)
        except Exception as exc:  # pragma: no cover - GUI boundary
            detail = f"{exc}\n{traceback.format_exc()}"
            self.signals.error.emit(detail)
        else:
            self.signals.result.emit(result)
        finally:
            self.signals.finished.emit()
