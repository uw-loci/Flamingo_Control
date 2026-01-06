# ============================================================================
# src/py2flamingo/services/communication/connection_manager.py
"""
Service for managing microscope communication.

ENHANCED: Now uses EnhancedThreadManager for consistent thread management.
"""

import socket
import threading
from queue import Queue, Empty
from typing import Optional, Callable, List, Dict, Any
import logging
import time
import numpy as np

from ..models.microscope import Position
from .communication.enhanced_thread_manager import EnhancedThreadManager


class ConnectionManager:
    """
    Manages TCP/IP communication with the microscope.

    This service handles all low-level communication including
    command sending, response handling, and data streaming.

    ENHANCED: Now uses centralized thread management for better control
    and monitoring of communication threads.
    """

    def __init__(self, ip_address: str, port: int):
        """
        Initialize connection manager.

        Args:
            ip_address: Microscope IP address
            port: Communication port
        """
        self.ip_address = ip_address
        self.port = port
        self.logger = logging.getLogger(__name__)

        # Socket and connection state
        self.socket: Optional[socket.socket] = None
        self.connected = False

        # Command queues
        self.command_queue = Queue()
        self.response_queue = Queue()

        # Data callbacks
        self.position_callbacks: List[Callable] = []
        self.image_callbacks: List[Callable] = []

        # ENHANCED: Use centralized thread manager
        self.thread_manager = EnhancedThreadManager(logger_name=__name__)

        # Command labels (from global_objects)
        self.command_labels = self._init_command_labels()
    
    def _init_command_labels(self) -> tuple:
        """Initialize command labels."""
        return (
            "COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD",
            "COMMAND_CODES_CAMERA_WORK_FLOW_START",
            "COMMAND_CODES_STAGE_POSITION_SET",
            "COMMAND_CODES_CAMERA_PIXEL_FIELD_Of_VIEW_GET",
            "COMMAND_CODES_CAMERA_IMAGE_SIZE_GET",
            "COMMAND_CODES_CAMERA_CHECK_STACK"
        )
    
    def connect(self):
        """Establish connection to microscope."""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(5.0)
            self.socket.connect((self.ip_address, self.port))
            
            self.connected = True
            self.logger.info(f"Connected to microscope at {self.ip_address}:{self.port}")
            
            # Start communication threads
            self._start_threads()
            
        except Exception as e:
            self.logger.error(f"Failed to connect: {e}")
            self.connected = False
            raise
    
    def disconnect(self):
        """Disconnect from microscope."""
        # ENHANCED: Use thread manager for graceful shutdown
        stop_results = self.thread_manager.stop_all(timeout=3.0)

        for name, stopped in stop_results.items():
            if not stopped:
                self.logger.warning(f"Thread '{name}' did not stop gracefully")

        if self.socket:
            try:
                self.socket.close()
            except:
                pass

        self.connected = False
        self.logger.info("Disconnected from microscope")
    
    def _start_threads(self):
        """Start communication threads using enhanced thread manager."""
        # ENHANCED: Use thread manager for all thread operations

        # Start command sender thread
        if not self.thread_manager.start_thread(
            name="command-sender",
            target=self._command_sender_loop,
            daemon=True
        ):
            self.logger.error("Failed to start command sender thread")
            raise RuntimeError("Failed to start communication threads")

        # Start response receiver thread
        if not self.thread_manager.start_thread(
            name="response-receiver",
            target=self._response_receiver_loop,
            daemon=True
        ):
            self.logger.error("Failed to start response receiver thread")
            raise RuntimeError("Failed to start communication threads")

        # Start data processor thread using queue processor helper
        # This demonstrates the enhanced pattern
        if not self.thread_manager.start_queue_processor(
            name="data-processor",
            input_queue=self.response_queue,
            processor=self._process_response_batch,
            batch_size=1,  # Process one at a time for now
            timeout=0.1
        ):
            self.logger.error("Failed to start data processor thread")
            raise RuntimeError("Failed to start communication threads")

        self.logger.info("All communication threads started successfully")
    
    def _command_sender_loop(self):
        """Send commands from queue to microscope."""
        # ENHANCED: Use thread manager's stop checking
        while not self.thread_manager.is_stopped():
            try:
                command = self.command_queue.get(timeout=0.1)
                if self.socket and self.connected:
                    self.socket.sendall(command.encode())
                    self.logger.debug(f"Sent command: {command}")
            except Empty:
                continue
            except Exception as e:
                self.logger.error(f"Error sending command: {e}")

    def _response_receiver_loop(self):
        """Receive responses from microscope."""
        # ENHANCED: Use thread manager's stop checking
        while not self.thread_manager.is_stopped():
            if not self.socket or not self.connected:
                # Use thread manager's wait for better shutdown response
                if self.thread_manager.wait_for_stop(0.1):
                    break
                continue

            try:
                self.socket.settimeout(0.1)
                data = self.socket.recv(4096)
                if data:
                    self.response_queue.put(data)
                    self.logger.debug(f"Received response: {len(data)} bytes")
            except socket.timeout:
                continue
            except Exception as e:
                self.logger.error(f"Error receiving response: {e}")

    def _process_response_batch(self, batch: List[bytes]) -> None:
        """
        Process a batch of responses (used by queue processor).

        ENHANCED: New method for batch processing pattern.

        Args:
            batch: List of response data bytes
        """
        for data in batch:
            try:
                self._process_response(data)
            except Exception as e:
                self.logger.error(f"Error processing response batch: {e}")
    
    def _process_response(self, data: bytes):
        """Process response data and trigger appropriate callbacks."""
        # Parse response type
        # This is simplified - actual implementation would parse protocol
        try:
            response_str = data.decode('utf-8', errors='ignore')
            
            # Check for position data
            if "POSITION:" in response_str:
                position_data = self._parse_position_data(response_str)
                if position_data:
                    for callback in self.position_callbacks:
                        callback(position_data)
            
            # Check for image data
            elif "IMAGE:" in response_str:
                # In reality, image data would be binary
                # This is simplified for demonstration
                pass
                
        except Exception as e:
            self.logger.error(f"Error parsing response: {e}")
    
    def _parse_position_data(self, response: str) -> Optional[List[float]]:
        """Parse position data from response."""
        try:
            # Example: "POSITION:X=10.5,Y=20.3,Z=5.1,R=45.0"
            if "POSITION:" in response:
                pos_str = response.split("POSITION:")[1].strip()
                parts = pos_str.split(",")
                position = []
                for part in parts:
                    if "=" in part:
                        value = float(part.split("=")[1])
                        position.append(value)
                if len(position) >= 4:
                    return position
        except:
            pass
        return None
    
    def subscribe_position_updates(self, callback: Callable[[List[float]], None]):
        """Subscribe to position updates."""
        self.position_callbacks.append(callback)
    
    def subscribe_image_updates(self, callback: Callable[[np.ndarray], None]):
        """Subscribe to image updates."""
        self.image_callbacks.append(callback)
    
    def send_command(self, command: str):
        """Send command to microscope."""
        self.command_queue.put(command)
    
    def send_move_command(self, position: Position):
        """Send move command."""
        command = f"MOVE:X={position.x},Y={position.y},Z={position.z},R={position.r}\n"
        self.send_command(command)
    
    # NOTE: send_workflow method removed - use WorkflowOrchestrator instead

    def send_emergency_stop(self):
        """Send emergency stop command."""
        self.send_command("EMERGENCY_STOP\n")
    
    def send_set_home_command(self, position: Position):
        """Send set home command."""
        command = f"SET_HOME:X={position.x},Y={position.y},Z={position.z},R={position.r}\n"
        self.send_command(command)
    
    def send_clear_home_command(self):
        """Send clear home command."""
        self.send_command("CLEAR_HOME\n")
    
    def send_filter_wheel_position(self, position: int, filter_type: str):
        """Send filter wheel position command."""
        command = f"FILTER:POS={position},TYPE={filter_type}\n"
        self.send_command(command)
    
    def send_illumination_path(self, path: str):
        """Send illumination path command."""
        command = f"ILLUMINATION_PATH:{path}\n"
        self.send_command(command)
    
    def send_camera_settings(self, settings: dict):
        """Send camera settings."""
        command = f"CAMERA_SETTINGS:{settings}\n"
        self.send_command(command)
    
    def send_led_settings(self, channel: str, intensity: float, 
                         pulse_mode: bool, pulse_duration: float):
        """Send LED settings."""
        command = (f"LED:CHANNEL={channel},INTENSITY={intensity},"
                  f"PULSE={pulse_mode},DURATION={pulse_duration}\n")
        self.send_command(command)
    
    def capture_single_image(self, laser_channel: str, laser_power: float) -> np.ndarray:
        """Capture single image."""
        # Send capture command
        command = f"CAPTURE:LASER={laser_channel},POWER={laser_power}\n"
        self.send_command(command)
        
        # Wait for image response
        # This is simplified - actual implementation would handle async response
        time.sleep(0.5)
        
        # Return dummy image for now
        return np.zeros((2048, 2048), dtype=np.uint16)
    
    def get_camera_info(self) -> dict:
        """Get camera information."""
        self.send_command("GET_CAMERA_INFO\n")
        
        # This is simplified - actual implementation would wait for response
        return {
            'pixel_size_mm': 0.00325,
            'frame_size': 2048,
            'bit_depth': 16
        }
    
    def get_workflow_status(self) -> dict:
        """Get current workflow status."""
        self.send_command("GET_WORKFLOW_STATUS\n")
        
        # This is simplified - actual implementation would wait for response
        return {
            'state': 'idle',
            'progress': 0
        }
    
    def stop_workflow(self):
        """Stop current workflow."""
        self.send_command("STOP_WORKFLOW\n")

    def get_thread_status(self) -> Dict[str, Any]:
        """
        Get status of all communication threads.

        ENHANCED: New method for thread health monitoring.

        Returns:
            Dictionary with thread status information
        """
        return self.thread_manager.get_all_status()

    def is_healthy(self) -> bool:
        """
        Check if all communication threads are healthy.

        ENHANCED: New health check method.

        Returns:
            True if all threads are running properly
        """
        if not self.connected:
            return False

        status = self.get_thread_status()
        for thread_name, thread_info in status.items():
            if thread_info.get('state') != 'running':
                self.logger.warning(f"Thread '{thread_name}' is not running: {thread_info.get('state')}")
                return False

        return True
