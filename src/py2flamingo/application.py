# src/py2flamingo/application.py
"""
Main application module for Py2Flamingo.
Sets up the main GUI and controllers.
"""

from PyQt5.QtWidgets import QApplication

from py2flamingo.GUI import Py2FlamingoGUI


class Application(Py2FlamingoGUI):
    """
    The main Application class for Py2Flamingo.
    
    This class inherits from Py2FlamingoGUI and is responsible for initializing
    and launching the Py2Flamingo application.
    """

    def __init__(self):
        """
        Initialize the Py2Flamingo application.
        
        This will set up the main GUI window and all necessary controllers and services.
        """
        super().__init__()
