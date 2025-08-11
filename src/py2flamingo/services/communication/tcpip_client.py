# src/py2flamingo/services/communication/tcp_client.py
"""
TCP client for low-level communication with the microscope.

This module provides the TCPClient class that handles socket-level
communication with the Flamingo microscope controller.
"""
import socket
import struct
import logging
import os
from typing import Optional, Tuple, List
import numpy as np

class TCPClient:
    """
    TCP client for communicating with Flamingo microscope.
    
    This class provides low-level socket communication methods
    for sending commands and workflows to the microscope.
    
    Attributes:
        ip: IP address of microscope
        port: Port number for connection
        nuc_socket: Socket for command communication
        live_socket: Socket for image data
        logger: Logger instance
    """
    
    def __init__(self, ip: str, port: int):
        """
        Initialize TCP client.
        
        Args:
            ip: IP address of microscope
            port: Port number for connection
        """
        self.ip = ip
        self.port = port
        self.port_listen = port + 1
        self.nuc_socket = None
        self.live_socket = None
        self.logger = logging.getLogger(__name__)
    
    def connect(self) -> Tuple[Optional[socket.socket], Optional[socket.socket]]:
        """
        Establish connection to microscope.
        
        Returns:
            Tuple[Optional[socket.socket], Optional[socket.socket]]: 
                NUC and live sockets if successful, (None, None) if failed
        """
        try:
            # Create sockets
            self.nuc_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.live_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            
            # Set timeout for connection
            self.nuc_socket.settimeout(2)
            
            # Connect to NUC
            self.logger.info(f"Connecting to NUC at {self.ip}:{self.port}")
            self.nuc_socket.connect((self.ip, self.port))
            
            # Connect to live data port
            self.logger.info(f"Connecting to live port at {self.ip}:{self.port_listen}")
            self.live_socket.connect((self.ip, self.port_listen))
            
            # Reset timeout
            self.nuc_socket.settimeout(None)
            
            self.logger.info("TCP connections established")
            return self.nuc_socket, self.live_socket
            
        except (socket.timeout, ConnectionRefusedError) as e:
            self.logger.error(f"Connection failed: {e}")
            self.disconnect()
            return None, None
        except Exception as e:
            self.logger.error(f"Unexpected error during connection: {e}")
            self.disconnect()
            return None, None
    
    def disconnect(self) -> None:
        """Close all connections."""
        if self.nuc_socket:
            try:
                self.nuc_socket.close()
            except:
                pass
            self.nuc_socket = None
            
        if self.live_socket:
            try:
                self.live_socket.close()
            except:
                pass
            self.live_socket = None
            
        self.logger.info("TCP connections closed")
    
    def send_command(self, command: int, command_data: List = None) -> None:
        """
        Send a command to the microscope.
        
        This method is based on the original command_to_nuc function
        from tcpip_nuc.py
        
        Args:
            command: Command code to send
            command_data: Optional list [data0, data1, data2, value]
        """
        if not self.nuc_socket:
            raise RuntimeError("Not connected to microscope")
        
        # Default command data if none provided
        if command_data is None:
            command_data = [0, 0, 0, 0.0]
        
        data0, data1, data2, value = command_data
        
        # Build command structure (based on original tcpip_nuc.py)
        cmd_start = np.uint32(0xF321E654)  # start command
        cmd = np.uint32(command)
        status = np.int32(0)
        hardwareID = np.int32(0)
        subsystemID = np.int32(0)
        clientID = np.int32(0)
        int32Data0 = np.int32(data0)
        int32Data1 = np.int32(data1)
        int32Data2 = np.int32(data2)
        cmdDataBits0 = np.uint32(0x80000000)
        doubleData = float(value)
        addDataBytes = np.int32(0)  # No additional data for commands
        buffer_72 = b"\0" * 72
        cmd_end = np.uint32(0xFEDC4321)  # end command
        
        # Pack to binary
        s = struct.Struct("I I I I I I I I I I d I 72s I")
        scmd = s.pack(
            cmd_start, cmd, status, hardwareID, subsystemID, clientID,
            int32Data0, int32Data1, int32Data2, cmdDataBits0,
            doubleData, addDataBytes, buffer_72, cmd_end
        )
        
        try:
            self.nuc_socket.send(scmd)
            self.logger.debug(f"Sent command {command} with data {command_data}")
        except socket.error as e:
            self.logger.error(f"Failed to send command: {e}")
            raise
    
    def send_workflow(self, workflow_file: str, command: int) -> None:
        """
        Send a workflow file to the microscope.
        
        This method is based on the original text_to_nuc function
        from tcpip_nuc.py
        
        Args:
            workflow_file: Path to workflow file
            command: Command code (usually COMMAND_CODES_CAMERA_WORK_FLOW_START)
        """
        if not self.nuc_socket:
            raise RuntimeError("Not connected to microscope")
        
        # Get file size
        fileBytes = os.path.getsize(workflow_file)
        
        # Build command structure
        cmd_start = np.uint32(0xF321E654)  # start command
        cmd = np.uint32(command)
        status = np.int32(0)
        hardwareID = np.int32(0)
        subsystemID = np.int32(0)
        clientID = np.int32(0)
        int32Data0 = np.int32(0)
        int32Data1 = np.int32(0)
        int32Data2 = np.int32(0)
        cmdDataBits0 = np.int32(1)
        doubleData = float(0)
        addDataBytes = np.int32(fileBytes)
        buffer_72 = b"\0" * 72
        cmd_end = np.uint32(0xFEDC4321)  # end command
        
        # Pack to binary
        s = struct.Struct("I I I I I I I I I I d I 72s I")
        scmd = s.pack(
            cmd_start, cmd, status, hardwareID, subsystemID, clientID,
            int32Data0, int32Data1, int32Data2, cmdDataBits0,
            doubleData, addDataBytes, buffer_72, cmd_end
        )
        
        try:
            # Read workflow file
            with open(workflow_file, 'rb') as f:
                workflow_data = f.read()
            
            # Send command header
            self.nuc_socket.send(scmd)
            
            # Send workflow data
            self.nuc_socket.send(workflow_data)
            
            self.logger.info(f"Sent workflow file {workflow_file}")
            
        except socket.error as e:
            self.logger.error(f"Failed to send workflow: {e}")
            raise
        except IOError as e:
            self.logger.error(f"Failed to read workflow file: {e}")
            raise
