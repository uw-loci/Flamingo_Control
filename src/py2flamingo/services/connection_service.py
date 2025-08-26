# src/py2flamingo/services/connection_service.py
"""
Service for managing microscope connections.

This service replaces the connection logic from FlamingoConnect and
provides a cleaner interface for connection management.
"""
import socket
import logging
import time
from typing import Optional, Tuple, List, Dict, Any
from threading import Thread
from pathlib import Path

from py2flamingo.core.events import EventManager
from py2flamingo.core.queue_manager import QueueManager
from .communication.thread_manager import ThreadManager

try:
    import src.py2flamingo.utils.file_handlers as fh
except Exception:
    from py2flamingo.utils import file_handlers as fh

class ConnectionService:
    """
    Service for managing connections to the microscope.
    
    This service handles establishing connections, managing communication
    threads, and coordinating data flow between the application and microscope.
    
    Attributes:
        ip: IP address of microscope
        port: Port number for connection
        event_manager: Event manager for synchronization
        queue_manager: Queue manager for data flow
        nuc_client: Socket for command communication
        live_client: Socket for image data
        thread_manager: Manager for communication threads
        logger: Logger instance
    """
    
    def __init__(self, ip: str, port: int, 
                 event_manager: EventManager, 
                 queue_manager: QueueManager):
        """
        Initialize the connection service.
        
        Args:
            ip: IP address of microscope
            port: Port number for connection
            event_manager: Event manager instance
            queue_manager: Queue manager instance
        """
        self.ip = ip
        self.port = port
        self.event_manager = event_manager
        self.queue_manager = queue_manager
        self.logger = logging.getLogger(__name__)
        
        # Connection state
        self.nuc_client = None
        self.live_client = None
        self.thread_manager = None
        self._connected = False
        
        # Connection data for backward compatibility
        self.connection_data = None
        self.threads = None
        
        # Default settings
        self.wf_zstack = "ZStack.txt"
        self.LED_on = "50.0 1"
        self.LED_off = "0.00 0"
    
    def connect(self) -> bool:
        """
        Establish connection to the microscope.
        
        Returns:
            bool: True if connection successful
        """
        try:
            self.logger.info(f"Connecting to microscope at {self.ip}:{self.port}")
            
            # Create sockets
            self.nuc_client = self._create_socket()
            self.live_client = self._create_socket()
            
            # Connect to microscope
            port_listen = self.port + 1
            
            try:
                self.nuc_client.settimeout(2)
                self.nuc_client.connect((self.ip, self.port))
                self.live_client.connect((self.ip, port_listen))
                
            except (socket.timeout, ConnectionRefusedError) as e:
                self.logger.error(f"Failed to connect: {e}")
                self._cleanup_sockets()
                return False
            
            # Store connection data for backward compatibility
            self.connection_data = [
                self.nuc_client, 
                self.live_client, 
                self.wf_zstack, 
                self.LED_on, 
                self.LED_off
            ]
            
            # Start communication threads
            self._start_threads()
            
            self._connected = True
            self.logger.info("Connection established successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"Connection failed: {e}")
            self._cleanup_sockets()
            return False
    
    def disconnect(self) -> None:
        """Disconnect from the microscope and cleanup."""
        if self._connected:
            self.logger.info("Disconnecting from microscope")
            
            # Stop threads
            if self.thread_manager:
                self.thread_manager.stop_all_threads()
            
            # Close sockets
            self._cleanup_sockets()
            
            self._connected = False
            self.logger.info("Disconnected successfully")
    
    def is_connected(self) -> bool:
        """
        Check if connected to microscope.
        
        Returns:
            bool: True if connected
        """
        return self._connected
    
    def get_connection_data(self) -> Optional[List]:
        """
        Get connection data for backward compatibility.
        
        Returns:
            Optional[List]: Connection data or None
        """
        return self.connection_data if self._connected else None
    
    def get_threads(self) -> Optional[Tuple]:
        """
        Get thread references for backward compatibility.
        
        Returns:
            Optional[Tuple]: Thread references or None
        """
        return self.threads if self._connected else None
    
    def send_command(self, command: int, data: Optional[List] = None) -> None:
        """
        Send a command to the microscope.
        
        Args:
            command: Command code
            data: Optional command data
        """
        if not self._connected:
            raise RuntimeError("Not connected to microscope")
        
        self.queue_manager.put_nowait('command', command)
        if data:
            self.queue_manager.put_nowait('command_data', data)
        self.event_manager.set_event('send')
    
    def send_workflow(self, workflow_dict: dict) -> None:
        """
        Send a workflow to the microscope.
        
        Args:
            workflow_dict: Workflow configuration dictionary
        """
        if not self._connected:
            raise RuntimeError("Not connected to microscope")
        
        # Save workflow to file (required by current implementation)
        from py2flamingo.utils.file_handlers import dict_to_workflow
        import os
        
        workflow_path = os.path.join('workflows', 'workflow.txt')
        fh.dict_to_workflow(workflow_path, workflow_dict)
        self.send_command(12292) 
        
        # Send workflow start command
       # COMMAND_CODES_CAMERA_WORK_FLOW_START = 12292
        #self.send_command(COMMAND_CODES_CAMERA_WORK_FLOW_START)
    
    def get_microscope_settings(self) -> Tuple[float, Dict[str, Any]]:
        """
        Retrieve microscope settings and image pixel size.
        
        This method replaces the function from microscope_connect.py
        
        Returns:
            Tuple[float, Dict]: Image pixel size and settings dictionary
        """
        # Send command to load settings
        COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD = 4105
        self.send_command(COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD)
        
        # Wait for settings to be saved
        time.sleep(0.5)
        
        # Load settings from file
        from py2flamingo.utils.file_handlers import text_to_dict
        settings_path = Path('microscope_settings') / 'ScopeSettings.txt'
        
        if not settings_path.exists():
            raise FileNotFoundError("Microscope settings not found")
        
        scope_settings = text_to_dict(str(settings_path))
        
        # Get pixel size
        COMMAND_CODES_CAMERA_PIXEL_FIELD_OF_VIEW_GET = 12347
        self.send_command(COMMAND_CODES_CAMERA_PIXEL_FIELD_OF_VIEW_GET)
        
        # Wait for response and get from queue
        time.sleep(0.2)
        image_pixel_size = self.queue_manager.get_nowait('other_data')
        if not image_pixel_size:
            # Use settings (mocked in the test)
            settings_path = Path('microscope_settings') / 'ScopeSettings.txt'
            scope_settings = fh.text_to_dict(str(settings_path))
            tube = float(scope_settings['Type']['Tube lens design focal length (mm)'])
            obj  = float(scope_settings['Type']['Objective lens magnification'])
            cam_um = 6.5
            image_pixel_size = (cam_um / (obj * (tube/200))) / 1000.0  # mm
        return image_pixel_size, scope_settings
    
    def _create_socket(self) -> socket.socket:
        """Create a TCP socket."""
        return socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    
    def _cleanup_sockets(self) -> None:
        """Close and cleanup sockets."""
        if self.nuc_client:
            try:
                self.nuc_client.close()
            except:
                pass
            self.nuc_client = None
            
        if self.live_client:
            try:
                self.live_client.close()
            except:
                pass
            self.live_client = None
    
    def _start_threads(self) -> None:
        """Start communication threads."""
        # Import thread functions
        from py2flamingo.services.communication.thread_manager import ThreadManager
        
        # Create thread manager
        self.thread_manager = ThreadManager(
            nuc_client=self.nuc_client,
            live_client=self.live_client,
            event_manager=self.event_manager,
            queue_manager=self.queue_manager
        )
        
        # Start threads
        self.threads = self.thread_manager.start_all_threads()
        
        self.logger.info("Communication threads started")
