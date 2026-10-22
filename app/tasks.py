"""Execução de trabalhos demorados (download, separação, sincronização) fora da
thread da interface, com diálogo de progresso opcional."""
from PySide6.QtCore import QObject, QThread
from PySide6.QtWidgets import QProgressDialog

_running = set()   # mantém as tarefas vivas até terminarem


def progress_dialog(parent, title, text, busy=False):
    """Diálogo de progresso que não fecha sozinho; "Ocultar" só o esconde."""
    dialog = QProgressDialog(text, "Ocultar", 0, 0 if busy else 100, parent)
    dialog.setWindowTitle(title)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    dialog.setMinimumDuration(0)
    dialog.setValue(0)
    dialog.show()
    return dialog


class _Task(QObject):
    """Liga um worker (QObject com run(), finished e failed) a uma QThread.

    O _Task vive na thread da interface, então os sinais do worker chegam a ele
    pela fila de eventos e os callbacks podem mexer na interface com segurança.
    """

    def __init__(self, parent, worker, on_finished, on_failed, dialog):
        super().__init__(parent)
        self._worker = worker
        self._on_finished_callback = on_finished
        self._on_failed_callback = on_failed
        self._dialog = dialog
        self._thread = QThread(self)

        worker.moveToThread(self._thread)
        self._thread.started.connect(worker.run)
        if dialog is not None and hasattr(worker, "progress"):
            worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._thread.quit)
        worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(worker.deleteLater)
        self._thread.finished.connect(self._release)

    def start(self):
        _running.add(self)
        self._thread.start()

    def _on_progress(self, value, message):
        if self._dialog is not None:
            self._dialog.setValue(value)
            self._dialog.setLabelText(message)

    def _close_dialog(self):
        if self._dialog is not None:
            self._dialog.close()
            self._dialog = None

    def _on_finished(self, *args):
        self._close_dialog()
        self._on_finished_callback(*args)

    def _on_failed(self, message):
        self._close_dialog()
        self._on_failed_callback(message)

    def _release(self):
        _running.discard(self)
        self.deleteLater()


def run_task(parent, worker, on_finished, on_failed, title=None, text="", busy=False):
    """Roda worker.run() em segundo plano.

    Com `title`, mostra um diálogo de progresso (barra, ou indeterminado com
    `busy=True`) que acompanha o sinal `progress(int, str)` do worker e fecha
    antes de `on_finished`/`on_failed` serem chamados.
    """
    dialog = progress_dialog(parent, title, text, busy) if title else None
    _Task(parent, worker, on_finished, on_failed, dialog).start()
