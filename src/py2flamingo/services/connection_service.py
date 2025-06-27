# ============================================================================
# src/py2flamingo/services/connection_service.py
"""
Service for managing microscope connections.

This service replaces the connection logic from FlamingoConnect and
provides a cleaner interface for connection management.
"""
import socket
import logging
from typing import Optional, Tuple, List
from threading import Thread

from py2flamingo.core.events import EventManager
from py2flamingo.core.queue_manager import QueueManager
from py2flamingo.services.communication.tcp_client import TCPClient
from py2flamingo.services.communication.thread_manager import ThreadManager

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
        tcp_client: TCP client for communication
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
        
        self.tcp_client = None
        self.thread_manager = None
        self._connected = False
        
        # Connection data for backward compatibility
        self.connection_data = None
        self.threads = None
    
    def connect(self) -> bool:
        """
        Establish connection to the microscope.
        
        Returns:
            bool: True if connection successful
        """
        try:
            self.logger.info(f"Connecting to microscope at {self.ip}:{self.port}")
            
            # Create TCP client
            self.tcp_client = TCPClient(self.ip, self.port)
            
            # Establish connections
            nuc_client, live_client = self.tcp_client.connect()
            
            if not nuc_client or not live_client:
                self.logger.error("Failed to establish connections")
                return False
            
            # Get workflow filename (for compatibility)
            wf_zstack = "ZStack.txt"
            LED_on = "50.0 1"
            LED_off = "0.00 0"
            
            # Store connection data for backward compatibility
            self.connection_data = [nuc_client, live_client, wf_zstack, LED_on, LED_off]
            
            # Create and start communication threads
            self.thread_manager = ThreadManager(
                nuc_client=nuc_client,
                live_client=live_client,
                event_manager=self.event_manager,
                queue_manager=self.queue_manager
            )
            
            self.threads = self.thread_manager.start_all_threads()
            
            self._connected = True
            self.logger.info("Connection established successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"Connection failed: {e}")
            return False
    
    def disconnect(self) -> None:
        """Disconnect from the microscope and cleanup."""
        if self._connected:
            self.logger.info("Disconnecting from microscope")
            
            if self.thread_manager:
                self.thread_manager.stop_all_threads()
            
            if self.tcp_client:
                self.tcp_client.disconnect()
            
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
        dict_to_workflow(workflow_path, workflow_dict)
        
        # Send workflow start command
        COMMAND_CODES_CAMERA_WORK_FLOW_START = 12292
        self.send_command(COMMAND_CODES_CAMERA_WORK_FLOW_START)_init__(self, base_path: Optional[str] = None):
        """
        Initialize the configuration service.
        
        Args:
            base_path: Base path for configuration files (defaults to current directory)
        """
        self.logger = logging.getLogger(__name__)
        self.base_path = Path(base_path) if base_path else Path.cwd()
        self.config: Dict[str, Any] = {}
        
        # Ensure required directories exist
        self._ensure_directories()
        
    def _ensure_directories(self) -> None:
        """Create required directories if they don't exist."""
        directories = [
            'workflows',
            'output_png',
            'sample_txt',
            'microscope_settings',
            'logs'
        ]
        
        for directory in directories:
            dir_path = self.base_path / directory
            if not dir_path.exists():
                dir_path.mkdir(parents=True, exist_ok=True)
                self.logger.info(f"Created directory: {dir_path}")
    
    def load_configuration(self) -> Dict[str, Any]:
        """
        Load all configuration files and validate setup.
        
        Returns:
            Dict: Complete configuration dictionary
            
        Raises:
            FileNotFoundError: If required files are missing
            ValueError: If configuration is invalid
        """
        try:
            # Load metadata file
            metadata = self._load_metadata_file()
            
            # Load workflow file
            workflow = self._load_workflow_file()
            
            # Load start position if available
            start_position = self._load_start_position(metadata['microscope_name'])
            
            # Load scope settings if available
            scope_settings = self._load_scope_settings()
            
            # Combine into configuration
            self.config = {
                'microscope_name': metadata['microscope_name'],
                'microscope_ip': metadata['microscope_ip'],
                'microscope_port': metadata['microscope_port'],
                'microscope_type': metadata['microscope_type'],
                'start_position': start_position,
                'default_laser': workflow.get('default_laser', 'Laser 3 488 nm'),
                'default_laser_power': workflow.get('default_laser_power', 5.0),
                'data_storage_location': workflow.get('data_storage_location', '/media/deploy/MSN_LS'),
                'scope_settings': scope_settings,
                'metadata': metadata,
                'workflow_template': workflow
            }
            
            self.logger.info("Configuration loaded successfully")
            return self.config
            
        except Exception as e:
            self.logger.error(f"Failed to load configuration: {e}")
            raise
    
    def _load_metadata_file(self) -> Dict[str, Any]:
        """
        Load and validate the FlamingoMetaData.txt file.
        
        Returns:
            Dict: Parsed metadata
            
        Raises:
            FileNotFoundError: If metadata file not found
        """
        metadata_path = self.base_path / 'microscope_settings' / 'FlamingoMetaData.txt'
        
        if not metadata_path.exists():
            self.logger.error(f"Metadata file not found at {metadata_path}")
            # Prompt user to locate file
            metadata_path = self._prompt_for_file(
                "FlamingoMetaData.txt",
                "Select Metadata Text File",
                "This file is generated when you run any workflow on the microscope."
            )
            
            if metadata_path:
                # Copy to expected location
                self._copy_file_to_settings(metadata_path, 'FlamingoMetaData.txt')
            else:
                raise FileNotFoundError("FlamingoMetaData.txt is required")
        
        # Parse metadata
        metadata_dict = text_to_dict(str(metadata_path))
        
        # Extract required information
        instrument_info = metadata_dict.get('Instrument', {}).get('Type', {})
        
        return {
            'microscope_name': instrument_info.get('Microscope name', 'Unknown'),
            'microscope_ip': instrument_info.get('Microscope address', '').split(' ')[0],
            'microscope_port': int(instrument_info.get('Microscope address', '53717').split(' ')[1]),
            'microscope_type': instrument_info.get('Microscope type', 'Unknown'),
            'objective_magnification': float(instrument_info.get('Objective lens magnification', 16)),
            'tube_lens_length': float(instrument_info.get('Tube lens length (mm)', 200)),
            'full_metadata': metadata_dict
        }
    
    def _load_workflow_file(self) -> Dict[str, Any]:
        """
        Load and validate the ZStack.txt workflow file.
        
        Returns:
            Dict: Parsed workflow settings
            
        Raises:
            FileNotFoundError: If workflow file not found
        """
        workflow_path = self.base_path / 'workflows' / 'ZStack.txt'
        
        if not workflow_path.exists():
            self.logger.error(f"Workflow file not found at {workflow_path}")
            # Prompt user to locate file
            source_path = self._prompt_for_file(
                "workflow.txt",
                "Select Workflow Text File",
                "Select a workflow file to use as the basis for settings."
            )
            
            if source_path:
                # Copy to expected location
                import shutil
                shutil.copy(source_path, workflow_path)
            else:
                raise FileNotFoundError("ZStack.txt workflow file is required")
        
        # Parse workflow
        workflow_dict = workflow_to_dict(str(workflow_path))
        
        # Extract laser settings
        illumination = workflow_dict.get('Illumination Source', {})
        lasers = [key for key in illumination.keys() if 'laser' in key.lower()]
        
        # Find active laser
        active_laser = None
        laser_power = 0.0
        for laser in lasers:
            settings = illumination[laser].split(' ')
            if len(settings) >= 2 and settings[1] == '1':
                active_laser = laser
                laser_power = float(settings[0])
                break
        
        return {
            'lasers': lasers,
            'default_laser': active_laser or (lasers[0] if lasers else 'Laser 3 488 nm'),
            'default_laser_power': laser_power,
            'data_storage_location': workflow_dict.get('Experiment Settings', {}).get(
                'Save image drive', '/media/deploy/MSN_LS'
            ),
            'full_workflow': workflow_dict
        }
    