# src/py2flamingo/services/communication/thread_manager.py
"""
Thread manager for microscope communication.

This module manages the communication threads that handle data
exchange with the microscope.
"""
import logging
import socket
from threading import Thread, Event
from typing import Tuple, Optional

from py2flamingo.core.events import EventManager
from py2flamingo.core.queue_manager import QueueManager

class ThreadManager:
    """
    Manages communication threads for microscope interaction.
    
    This class creates and manages the threads that handle:
    - Command sending
    - Command response listening
    - Image data receiving
    - Data processing
    
    Attributes:
        nuc_client: Socket for command communication
        live_client: Socket for image data
        event_manager: Event manager for synchronization
        queue_manager: Queue manager for data flow
        threads: List of active threads
        logger: Logger instance
    """
    
    def __init__(self, 
                 nuc_client: socket.socket,
                 live_client: socket.socket,
                 event_manager: EventManager,
                 queue_manager: QueueManager):
        """
        Initialize the thread manager.
        
        Args:
            nuc_client: Socket for command communication
            live_client: Socket for image data
            event_manager: Event manager instance
            queue_manager: Queue manager instance
        """
        self.nuc_client = nuc_client
        self.live_client = live_client
        self.event_manager = event_manager
        self.queue_manager = queue_manager
        self.logger = logging.getLogger(__name__)
        self.threads = []
    
    def start_all_threads(self) -> Tuple[Thread, ...]:
        """
        Start all communication threads.
        
        Returns:
            Tuple of thread objects
        """
        # Import thread functions from existing code
        from py2flamingo.functions.threads import (
            command_listen_thread,
            live_listen_thread,
            send_thread,
            processing_thread
        )
        
        # Create threads
        threads = [
            # Thread for listening to command responses
            Thread(
                target=command_listen_thread,
                args=(
                    self.nuc_client,
                    self.event_manager.get_event('system_idle'),
                    self.event_manager.get_event('terminate'),
                    self.queue_manager.get_queue('other_data')
                ),
                name="CommandListenThread"
            ),
            
            # Thread for receiving image data
            Thread(
                target=live_listen_thread,
                args=(
                    self.live_client,
                    self.event_manager.get_event('terminate'),
                    self.queue_manager.get_queue('image'),
                    self.queue_manager.get_queue('visualize')
                ),
                name="LiveListenThread"
            ),
            
            # Thread for sending commands
            Thread(
                target=send_thread,
                args=(
                    self.nuc_client,
                    self.queue_manager.get_queue('command'),
                    self.event_manager.get_event('send'),
                    self.event_manager.get_event('system_idle'),
                    self.queue_manager.get_queue('command_data')
                ),
                name="SendThread"
            ),
            
            # Thread for processing data
            Thread(
                target=processing_thread,
                args=(
                    self.queue_manager.get_queue('z_plane'),
                    self.event_manager.get_event('terminate'),
                    self.event_manager.get_event('processing'),
                    self.queue_manager.get_queue('intensity'),
                    self.queue_manager.get_queue('image')
                ),
                name="ProcessingThread"
            )
        ]
        
        # Set daemon flag and start threads
        for thread in threads:
            thread.daemon = True
            thread.start()
            self.logger.info(f"Started thread: {thread.name}")
        
        self.threads = threads
        return tuple(threads)
    
    def stop_all_threads(self) -> None:
        """Stop all communication threads."""
        self.logger.info("Stopping communication threads...")
        
        # Set terminate event
        self.event_manager.set_event('terminate')
        
        # Wait for threads to finish (with timeout)
        for thread in self.threads:
            thread.join(timeout=2.0)
            if thread.is_alive():
                self.logger.warning(f"Thread {thread.name} did not stop cleanly")
        
        # Clear terminate event
        self.event_manager.clear_event('terminate')
        
        self.logger.info("All threads stopped")
