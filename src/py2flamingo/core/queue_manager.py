# src/py2flamingo/core/queue_manager.py
"""
Queue manager for inter-thread communication.

This module replaces the global queue objects with a managed approach.
"""
from queue import Queue, Empty
from typing import Dict, Any, Optional
import logging

class QueueManager:
    """
    Manages queues for inter-thread communication.
    
    This class centralizes queue management, replacing global queue objects
    with a more maintainable approach.
    
    Attributes:
        _queues: Dictionary of managed queues
        logger: Logger instance
    """
    
    def __init__(self):
        """Initialize the queue manager with standard queues."""
        self.logger = logging.getLogger(__name__)
        
        # Create all queues that were in global_objects.py
        self._queues: Dict[str, Queue] = {
            'image': Queue(),           # Image data from camera
            'command': Queue(),         # Commands to send to microscope
            'command_data': Queue(),    # Data associated with commands
            'z_plane': Queue(),         # Z-plane data for processing
            'intensity': Queue(),       # Intensity data from processing
            'visualize': Queue(),       # Images for visualization
            'stage_location': Queue(),  # Stage position updates
            'other_data': Queue(),      # Miscellaneous data from microscope
        }
        
        self.logger.debug(f"Initialized {len(self._queues)} queues")
    
    def get_queue(self, name: str) -> Queue:
        """
        Get a queue by name.
        
        Args:
            name: Name of the queue
            
        Returns:
            Queue: The requested queue
            
        Raises:
            KeyError: If queue name doesn't exist
        """
        if name not in self._queues:
            raise KeyError(f"Queue '{name}' not found. Available queues: {list(self._queues.keys())}")
        return self._queues[name]
    
    def put_nowait(self, queue_name: str, item: Any) -> None:
        """
        Put an item in a queue without blocking.
        
        Args:
            queue_name: Name of the queue
            item: Item to put in the queue
            
        Raises:
            KeyError: If queue doesn't exist
            queue.Full: If queue is full
        """
        queue = self.get_queue(queue_name)
        queue.put_nowait(item)
        self.logger.debug(f"Put item in queue '{queue_name}'")
    
    def get_nowait(self, queue_name: str) -> Optional[Any]:
        """
        Get an item from a queue without blocking.
        
        Args:
            queue_name: Name of the queue
            
        Returns:
            Optional[Any]: Item from queue or None if empty
        """
        try:
            queue = self.get_queue(queue_name)
            item = queue.get_nowait()
            self.logger.debug(f"Got item from queue '{queue_name}'")
            return item
        except Empty:
            return None
        except KeyError as e:
            self.logger.error(f"Queue error: {e}")
            return None
    
    def clear_queue(self, name: str) -> None:
        """
        Clear all items from a specific queue.
        
        Args:
            name: Name of the queue to clear
        """
        try:
            queue = self.get_queue(name)
            cleared = 0
            
            # Empty the queue
            while not queue.empty():
                try:
                    queue.get_nowait()
                    cleared += 1
                except Empty:
                    break
            
            self.logger.debug(f"Cleared {cleared} items from queue '{name}'")
            
        except KeyError as e:
            self.logger.error(f"Cannot clear queue: {e}")
    
    def clear_all(self) -> None:
        """Clear all items from all queues."""
        for name in self._queues:
            self.clear_queue(name)
        self.logger.info("All queues cleared")
    
    def get_queue_size(self, name: str) -> int:
        """
        Get approximate size of a queue.
        
        Args:
            name: Name of the queue
            
        Returns:
            int: Approximate number of items in queue
        """
        try:
            return self.get_queue(name).qsize()
        except KeyError:
            return 0
    
    def get_all_sizes(self) -> Dict[str, int]:
        """
        Get sizes of all queues.
        
        Returns:
            Dict[str, int]: Queue names to sizes
        """
        return {name: queue.qsize() for name, queue in self._queues.items()}