"""The last-resort error handler must never become a crash itself."""

import threading
import unittest
from unittest import mock

from PyQt6.QtCore import QEventLoop, QThread, QTimer
from PyQt6.QtWidgets import QApplication

import ui.main_window as main_window


def run_events(ms=300):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


class ErrorHandlerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.shown_on_gui_thread = []

        def fake_critical(*args, **kwargs):
            self.shown_on_gui_thread.append(
                threading.current_thread() is threading.main_thread()
            )

        patcher = mock.patch.object(main_window.QMessageBox, "critical", fake_critical)
        patcher.start()
        self.addCleanup(patcher.stop)

        log = mock.patch.object(main_window, "data_file", return_value=self._log_path())
        log.start()
        self.addCleanup(log.stop)

        import sys
        self.addCleanup(setattr, sys, "excepthook", sys.excepthook)
        main_window.install_error_handler()

    def _log_path(self):
        import tempfile
        from pathlib import Path
        return Path(tempfile.mkdtemp()) / "crash.log"

    def test_exception_in_a_background_thread_is_shown_on_the_gui_thread(self):
        class Boom(QThread):
            def run(self):
                raise RuntimeError("background failure")

        thread = Boom()
        thread.start()
        thread.wait(3000)
        run_events()
        self.assertEqual(self.shown_on_gui_thread, [True])

    def test_exception_in_a_slot_is_shown_and_the_app_keeps_running(self):
        def fail():
            raise RuntimeError("slot failure")

        QTimer.singleShot(0, fail)
        run_events()
        self.assertEqual(self.shown_on_gui_thread, [True])


if __name__ == "__main__":
    unittest.main()
