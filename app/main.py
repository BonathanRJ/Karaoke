"""Ponto de entrada do karaokê (python -m app.main)."""
import sys

from PySide6.QtWidgets import QApplication

from .database import init_db
from .main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    init_db()
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
