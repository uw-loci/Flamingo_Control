"""
Main entry point to run Py2Flamingo as a script.
"""
#TODO where did setup logging go?
import sys
from PyQt5.QtWidgets import QApplication

from py2flamingo import Application

if __name__ == "__main__":
    # Launch the PyQt application for Py2Flamingo
    app = QApplication(sys.argv)
    flamingo_app = Application()
    flamingo_app.show()
    sys.exit(app.exec_())
