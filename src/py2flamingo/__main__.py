# src/py2flamingo/__main__.py
"""
Main entry point for the Py2Flamingo application.

This module initializes the application and launches either the standalone
GUI or the Napari plugin interface based on command line arguments.
"""
import sys
import os
import logging
import argparse
from typing import Optional

def setup_logging(log_level: str = "INFO") -> None:
    """
    Configure application-wide logging.
    
    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
    """
    log_dir = os.path.join(os.getcwd(), 'logs')
    os.makedirs(log_dir, exist_ok=True)
    
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(os.path.join(log_dir, 'py2flamingo.log')),
            logging.StreamHandler()
        ]
    )

def run_napari_interface():
    """
    Launch Py2Flamingo within a Napari viewer.
    
    This is the primary entry point for the application, providing
    the full GUI within the Napari environment.
    """
    logger = logging.getLogger(__name__)
    
    try:
        import napari
        from PyQt5.QtWidgets import QApplication
        
        # Import our Napari-integrated GUI
        from .napari import NapariFlamingoGui
        from .application import Application
        
        # Ensure QApplication exists for dialogs
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)
        
        # Create application coordinator
        application = Application()
        
        # Initialize application (loads config, creates services, etc.)
        logger.info("Initializing Py2Flamingo application...")
        if not application.initialize():
            logger.error("Failed to initialize application")
            sys.exit(1)
        
        # Create Napari viewer
        viewer = napari.Viewer(title="Py2Flamingo - Flamingo Microscope Control")
        
        # Create our GUI widget
        controller = NapariFlamingoGui(
            application.get_legacy_queues_and_events(),
            viewer
        )
        
        # Add widget to Napari as a dock widget
        viewer.window.add_dock_widget(
            controller, 
            area='right',
            name='Flamingo Control'
        )
        
        logger.info("Py2Flamingo started successfully in Napari")
        
        # Run Napari
        napari.run()
        
    except ImportError as e:
        logger.error(f"Failed to import required module: {e}")
        logger.error("Please ensure napari is installed: pip install napari")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to start Napari interface: {e}")
        sys.exit(1)
    finally:
        # Clean shutdown
        if 'application' in locals():
            application.shutdown()

def run_standalone_gui():
    """
    Launch the standalone PyQt5 GUI (legacy mode).
    
    This mode runs the GUI without Napari integration.
    """
    logger = logging.getLogger(__name__)
    
    try:
        from PyQt5.QtWidgets import QApplication
        from .GUI import GUI as Py2FlamingoGUI
        from .application import Application
        
        # Create Qt application
        app = QApplication(sys.argv)
        app.setApplicationName("Py2Flamingo")
        
        # Create application coordinator
        application = Application()
        
        # Initialize application
        logger.info("Initializing Py2Flamingo application...")
        if not application.initialize():
            logger.error("Failed to initialize application")
            sys.exit(1)
        
        # Create and show GUI
        controller = Py2FlamingoGUI(application.get_legacy_queues_and_events())
        controller.show()
        
        logger.info("Py2Flamingo started successfully in standalone mode")
        
        # Run application
        sys.exit(app.exec_())
        
    except Exception as e:
        logger.error(f"Failed to start standalone GUI: {e}")
        sys.exit(1)
    finally:
        # Clean shutdown
        if 'application' in locals():
            application.shutdown()

def main():
    """Main entry point with command line argument parsing."""
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Py2Flamingo - Control software for Flamingo microscopes"
    )
    parser.add_argument(
        '--mode',
        choices=['napari', 'standalone'],
        default='napari',
        help='Launch mode: napari (default) or standalone'
    )
    parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        default='INFO',
        help='Logging level (default: INFO)'
    )
    parser.add_argument(
        '--config',
        type=str,
        help='Path to configuration directory (default: current directory)'
    )
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging(args.log_level)
    logger = logging.getLogger(__name__)
    
    # Set configuration path if provided
    if args.config:
        os.environ['PY2FLAMINGO_CONFIG_PATH'] = args.config
    
    logger.info(f"Starting Py2Flamingo in {args.mode} mode")
    
    # Launch appropriate interface
    if args.mode == 'napari':
        run_napari_interface()
    else:
        run_standalone_gui()

if __name__ == "__main__":
    main()
