# tests/test_queue_event_management.py
"""
Unit tests for queue and event management systems.

These tests verify the core threading and synchronization mechanisms.
"""
import unittest
import threading
import time
from queue import Empty

from py2flamingo.core.queue_manager import QueueManager
from py2flamingo.core.events import EventManager


class TestQueueManager(unittest.TestCase):
    """Test the QueueManager class."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.qm = QueueManager()
    
    def test_queue_creation(self):
        """Test that all expected queues are created."""
        expected_queues = [
            'image', 'command', 'command_data', 'z_plane',
            'intensity', 'visualize', 'stage_location', 'other_data'
        ]
        
        for queue_name in expected_queues:
            queue = self.qm.get_queue(queue_name)
            self.assertIsNotNone(queue)
            self.assertTrue(queue.empty())
    
    def test_put_and_get_nowait(self):
        """Test non-blocking put and get operations."""
        # Put item
        test_data = {'test': 'data'}
        self.qm.put_nowait('command', test_data)
        
        # Get item
        result = self.qm.get_nowait('command')
        self.assertEqual(result, test_data)
        
        # Queue should be empty now
        result = self.qm.get_nowait('command')
        self.assertIsNone(result)
    
    def test_queue_not_found(self):
        """Test handling of non-existent queue."""
        with self.assertRaises(KeyError):
            self.qm.get_queue('non_existent_queue')
        
        # get_nowait should return None for non-existent queue
        result = self.qm.get_nowait('non_existent_queue')
        self.assertIsNone(result)
    
    def test_clear_queue(self):
        """Test clearing a specific queue."""
        # Add multiple items
        for i in range(5):
            self.qm.put_nowait('image', f'image_{i}')
        
        # Verify queue has items
        self.assertEqual(self.qm.get_queue_size('image'), 5)
        
        # Clear queue
        self.qm.clear_queue('image')
        
        # Verify queue is empty
        self.assertEqual(self.qm.get_queue_size('image'), 0)
        self.assertIsNone(self.qm.get_nowait('image'))
    
    def test_clear_all(self):
        """Test clearing all queues."""
        # Add items to multiple queues
        self.qm.put_nowait('command', 'cmd1')
        self.qm.put_nowait('image', 'img1')
        self.qm.put_nowait('visualize', 'viz1')
        
        # Clear all
        self.qm.clear_all()
        
        # Verify all are empty
        sizes = self.qm.get_all_sizes()
        for queue_name, size in sizes.items():
            self.assertEqual(size, 0)
    
    def test_thread_safety(self):
        """Test thread-safe access to queues."""
        results = []
        errors = []
        
        def producer(queue_name, count):
            """Add items to queue."""
            try:
                for i in range(count):
                    self.qm.put_nowait(queue_name, f'item_{i}')
                    time.sleep(0.001)  # Small delay
            except Exception as e:
                errors.append(e)
        
        def consumer(queue_name, count):
            """Get items from queue."""
            try:
                received = []
                for _ in range(count):
                    item = None
                    while item is None:
                        item = self.qm.get_nowait(queue_name)
                        if item is None:
                            time.sleep(0.001)
                    received.append(item)
                results.append(received)
            except Exception as e:
                errors.append(e)
        
        # Create threads
        producer_thread = threading.Thread(target=producer, args=('command', 10))
        consumer_thread = threading.Thread(target=consumer, args=('command', 10))
        
        # Run threads
        producer_thread.start()
        consumer_thread.start()
        
        # Wait for completion
        producer_thread.join()
        consumer_thread.join()
        
        # Verify no errors
        self.assertEqual(len(errors), 0)
        
        # Verify all items received
        self.assertEqual(len(results), 1)
        self.assertEqual(len(results[0]), 10)


class TestEventManager(unittest.TestCase):
    """Test the EventManager class."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.em = EventManager()
    
    def test_event_creation(self):
        """Test that all expected events are created."""
        expected_events = [
            'view_snapshot', 'system_idle', 'processing',
            'send', 'terminate', 'visualize'
        ]
        
        for event_name in expected_events:
            event = self.em.get_event(event_name)
            self.assertIsNotNone(event)
    
    def test_initial_state(self):
        """Test initial event states."""
        # system_idle should be set initially
        self.assertTrue(self.em.is_set('system_idle'))
        
        # Others should be clear
        self.assertFalse(self.em.is_set('processing'))
        self.assertFalse(self.em.is_set('terminate'))
    
    def test_set_and_clear(self):
        """Test setting and clearing events."""
        # Set event
        self.em.set_event('processing')
        self.assertTrue(self.em.is_set('processing'))
        
        # Clear event
        self.em.clear_event('processing')
        self.assertFalse(self.em.is_set('processing'))
    
    def test_event_not_found(self):
        """Test handling of non-existent event."""
        with self.assertRaises(KeyError):
            self.em.get_event('non_existent_event')
        
        # is_set should return False for non-existent event
        self.assertFalse(self.em.is_set('non_existent_event'))
    
    def test_wait_for_event(self):
        """Test waiting for an event."""
        # Test immediate return for set event
        self.em.set_event('send')
        result = self.em.wait_for_event('send', timeout=1.0)
        self.assertTrue(result)
        
        # Test timeout for clear event
        self.em.clear_event('send')
        start_time = time.time()
        result = self.em.wait_for_event('send', timeout=0.1)
        elapsed = time.time() - start_time
        
        self.assertFalse(result)
        self.assertGreater(elapsed, 0.09)  # Should wait ~0.1s
        self.assertLess(elapsed, 0.2)      # But not much longer
    
    def test_clear_all(self):
        """Test clearing all events."""
        # Set multiple events
        self.em.set_event('processing')
        self.em.set_event('visualize')
        self.em.set_event('send')
        
        # Clear all
        self.em.clear_all()
        
        # Verify all cleared except system_idle
        states = self.em.get_event_states()
        for event_name, is_set in states.items():
            if event_name == 'system_idle':
                self.assertTrue(is_set)
            else:
                self.assertFalse(is_set)
    
    def test_thread_synchronization(self):
        """Test event-based thread synchronization."""
        results = []
        
        def waiter(event_name, timeout=2.0):
            """Wait for event and record result."""
            result = self.em.wait_for_event(event_name, timeout)
            results.append(result)
        
        def setter(event_name, delay=0.1):
            """Set event after delay."""
            time.sleep(delay)
            self.em.set_event(event_name)
        
        # Create threads
        wait_thread = threading.Thread(target=waiter, args=('send',))
        set_thread = threading.Thread(target=setter, args=('send', 0.05))
        
        # Start threads
        wait_thread.start()
        set_thread.start()
        
        # Wait for completion
        wait_thread.join()
        set_thread.join()
        
        # Verify event was received
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0])


class TestLegacyAdapter(unittest.TestCase):
    """Test the legacy adapter for backward compatibility."""
    
    def test_legacy_imports(self):
        """Test that legacy global objects can be imported."""
        # Import legacy objects
        from py2flamingo.core.legacy_adapter import (
            image_queue, command_queue, send_event, system_idle,
            clear_all_events_queues, OS,
        )
        
        # Verify queues exist and are Queue objects
        self.assertTrue(hasattr(image_queue, 'put'))
        self.assertTrue(hasattr(image_queue, 'get'))
        self.assertTrue(hasattr(command_queue, 'put'))
        
        # Verify events exist and are Event objects
        self.assertTrue(hasattr(send_event, 'set'))
        self.assertTrue(hasattr(send_event, 'wait'))
        self.assertTrue(hasattr(system_idle, 'is_set'))
        
        # Test clear function
        image_queue.put('test')
        send_event.set()
        
        clear_all_events_queues()
        
        self.assertTrue(image_queue.empty())
        self.assertFalse(send_event.is_set())
        self.assertTrue(system_idle.is_set())  # Should be set after clear
        
        # Test OS
        self.assertIn(OS, ['Windows', 'Linux', 'Darwin'])
    
    def test_singleton_behavior(self):
        """Test that multiple imports get the same objects."""
        # Import twice
        from py2flamingo.core.legacy_adapter import image_queue as q1
        from py2flamingo.core.legacy_adapter import image_queue as q2
        
        # Should be the same object
        self.assertIs(q1, q2)
        
        # Test with data
        q1.put('test_data')
        result = q2.get()
        self.assertEqual(result, 'test_data')


if __name__ == '__main__':
    unittest.main()
