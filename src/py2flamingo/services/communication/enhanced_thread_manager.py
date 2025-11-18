"""
Enhanced thread manager for consolidated thread handling.

This module provides a unified interface for all thread management in the
Flamingo Control application, replacing various ad-hoc threading patterns.
"""

import threading
import logging
from typing import Callable, Optional, Dict, List, Any
from queue import Queue, Empty
import time
from dataclasses import dataclass
from enum import Enum


class ThreadState(Enum):
    """Thread states for monitoring."""
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class ThreadInfo:
    """Information about a managed thread."""
    name: str
    thread: threading.Thread
    state: ThreadState
    target: Callable
    args: tuple
    kwargs: dict
    error: Optional[Exception] = None
    start_time: Optional[float] = None
    stop_time: Optional[float] = None


class EnhancedThreadManager:
    """
    Unified thread manager for all Flamingo Control threading needs.

    Features:
    - Centralized thread lifecycle management
    - Thread health monitoring
    - Graceful shutdown with timeout
    - Error tracking and recovery
    - Support for both legacy and modern patterns
    """

    def __init__(self, logger_name: str = "ThreadManager"):
        """Initialize the enhanced thread manager."""
        self.logger = logging.getLogger(logger_name)
        self._threads: Dict[str, ThreadInfo] = {}
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # Allow dependency injection for testing
        self.custom_targets: Dict[str, Callable] = {}

        # Thread monitoring
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_interval = 1.0  # seconds

    def register_custom_target(self, name: str, target: Callable):
        """
        Register a custom thread target for testing or override.

        Args:
            name: Name identifier for the thread
            target: Callable to run in the thread
        """
        self.custom_targets[name] = target
        self.logger.debug(f"Registered custom target for thread: {name}")

    def start_thread(
        self,
        name: str,
        target: Callable,
        args: tuple = (),
        kwargs: dict = None,
        daemon: bool = True,
        use_custom: bool = True
    ) -> bool:
        """
        Start a new managed thread.

        Args:
            name: Unique name for the thread
            target: Function to run in the thread
            args: Positional arguments for target
            kwargs: Keyword arguments for target
            daemon: Whether thread should be daemon
            use_custom: Whether to use custom target if registered

        Returns:
            True if thread started successfully
        """
        with self._lock:
            # Check if thread already exists and is running
            if name in self._threads:
                existing = self._threads[name]
                if existing.thread.is_alive():
                    self.logger.warning(f"Thread '{name}' already running")
                    return False
                else:
                    # Clean up dead thread
                    del self._threads[name]

            # Use custom target if available and requested
            if use_custom and name in self.custom_targets:
                target = self.custom_targets[name]
                self.logger.debug(f"Using custom target for thread: {name}")

            kwargs = kwargs or {}

            # Create thread info
            thread_info = ThreadInfo(
                name=name,
                thread=None,  # Will be set below
                state=ThreadState.STARTING,
                target=target,
                args=args,
                kwargs=kwargs,
                start_time=time.time()
            )

            # Create and start thread
            try:
                # Wrap target to handle errors and state
                wrapped_target = self._wrap_target(name, target)
                thread = threading.Thread(
                    target=wrapped_target,
                    args=args,
                    kwargs=kwargs,
                    name=name,
                    daemon=daemon
                )
                thread_info.thread = thread
                thread.start()
                thread_info.state = ThreadState.RUNNING
                self._threads[name] = thread_info

                self.logger.info(f"Started thread: {name}")
                return True

            except Exception as e:
                self.logger.error(f"Failed to start thread '{name}': {e}")
                thread_info.state = ThreadState.ERROR
                thread_info.error = e
                return False

    def _wrap_target(self, name: str, target: Callable) -> Callable:
        """
        Wrap thread target to handle errors and state updates.

        Args:
            name: Thread name
            target: Original target function

        Returns:
            Wrapped function
        """
        def wrapper(*args, **kwargs):
            try:
                # Update state
                with self._lock:
                    if name in self._threads:
                        self._threads[name].state = ThreadState.RUNNING

                # Run target
                result = target(*args, **kwargs)

                # Update state on completion
                with self._lock:
                    if name in self._threads:
                        self._threads[name].state = ThreadState.STOPPED
                        self._threads[name].stop_time = time.time()

                return result

            except Exception as e:
                self.logger.error(f"Thread '{name}' crashed: {e}")
                with self._lock:
                    if name in self._threads:
                        self._threads[name].state = ThreadState.ERROR
                        self._threads[name].error = e
                        self._threads[name].stop_time = time.time()
                raise

        return wrapper

    def stop_thread(self, name: str, timeout: float = 2.0) -> bool:
        """
        Stop a specific thread.

        Args:
            name: Name of thread to stop
            timeout: Maximum time to wait for thread to stop

        Returns:
            True if thread stopped successfully
        """
        with self._lock:
            if name not in self._threads:
                self.logger.warning(f"Thread '{name}' not found")
                return False

            thread_info = self._threads[name]
            if not thread_info.thread.is_alive():
                self.logger.info(f"Thread '{name}' already stopped")
                return True

            thread_info.state = ThreadState.STOPPING

        # Signal stop event (threads should check this)
        self._stop_event.set()

        # Wait for thread to stop
        thread_info.thread.join(timeout=timeout)

        if thread_info.thread.is_alive():
            self.logger.error(f"Thread '{name}' failed to stop within {timeout}s")
            return False

        with self._lock:
            thread_info.state = ThreadState.STOPPED
            thread_info.stop_time = time.time()

        self.logger.info(f"Thread '{name}' stopped")
        return True

    def stop_all(self, timeout: float = 5.0) -> Dict[str, bool]:
        """
        Stop all managed threads.

        Args:
            timeout: Maximum time to wait for all threads

        Returns:
            Dictionary of thread names to stop success status
        """
        self.logger.info("Stopping all threads...")
        self._stop_event.set()

        results = {}
        timeout_per_thread = timeout / max(len(self._threads), 1)

        for name in list(self._threads.keys()):
            results[name] = self.stop_thread(name, timeout_per_thread)

        self._stop_event.clear()
        return results

    def get_thread_status(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Get status information for a thread.

        Args:
            name: Thread name

        Returns:
            Status dictionary or None if not found
        """
        with self._lock:
            if name not in self._threads:
                return None

            info = self._threads[name]
            runtime = None
            if info.start_time:
                if info.stop_time:
                    runtime = info.stop_time - info.start_time
                else:
                    runtime = time.time() - info.start_time

            return {
                'name': info.name,
                'state': info.state.value,
                'alive': info.thread.is_alive(),
                'daemon': info.thread.daemon,
                'runtime': runtime,
                'error': str(info.error) if info.error else None
            }

    def get_all_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status for all threads."""
        return {name: self.get_thread_status(name)
                for name in self._threads.keys()}

    def is_stopped(self) -> bool:
        """Check if stop event has been set."""
        return self._stop_event.is_set()

    def wait_for_stop(self, timeout: float = 0.1):
        """
        Wait for stop event with timeout.

        Useful in thread loops to check for shutdown.

        Args:
            timeout: Maximum time to wait

        Returns:
            True if stop event was set
        """
        return self._stop_event.wait(timeout)

    # Convenience methods for common thread patterns

    def start_queue_processor(
        self,
        name: str,
        input_queue: Queue,
        processor: Callable,
        output_queue: Optional[Queue] = None,
        batch_size: int = 1,
        timeout: float = 0.1
    ) -> bool:
        """
        Start a thread that processes items from a queue.

        Args:
            name: Thread name
            input_queue: Queue to read from
            processor: Function to process items
            output_queue: Optional queue for results
            batch_size: Number of items to process at once
            timeout: Queue get timeout

        Returns:
            True if started successfully
        """
        def queue_worker():
            batch = []
            while not self._stop_event.is_set():
                try:
                    # Get items from queue
                    item = input_queue.get(timeout=timeout)
                    batch.append(item)

                    # Process when batch is full
                    if len(batch) >= batch_size:
                        result = processor(batch)
                        if output_queue and result is not None:
                            output_queue.put(result)
                        batch = []

                except Empty:
                    # Process partial batch if exists
                    if batch:
                        result = processor(batch)
                        if output_queue and result is not None:
                            output_queue.put(result)
                        batch = []
                except Exception as e:
                    self.logger.error(f"Queue processor '{name}' error: {e}")

        return self.start_thread(name, queue_worker)

    def start_periodic_task(
        self,
        name: str,
        task: Callable,
        interval: float,
        run_immediately: bool = True
    ) -> bool:
        """
        Start a thread that runs a task periodically.

        Args:
            name: Thread name
            task: Function to run periodically
            interval: Time between runs (seconds)
            run_immediately: Whether to run task immediately

        Returns:
            True if started successfully
        """
        def periodic_worker():
            if run_immediately:
                try:
                    task()
                except Exception as e:
                    self.logger.error(f"Periodic task '{name}' error: {e}")

            while not self._stop_event.is_set():
                if self._stop_event.wait(interval):
                    break  # Stop event was set
                try:
                    task()
                except Exception as e:
                    self.logger.error(f"Periodic task '{name}' error: {e}")

        return self.start_thread(name, periodic_worker)

    # Legacy compatibility methods

    def start_receivers(self, *args):
        """Legacy compatibility: start command receiver thread."""
        if len(args) >= 4:
            # Expected args: (socket, queues, events, etc.)
            def receiver_loop():
                # Implement receiver logic or delegate to legacy
                pass
            return self.start_thread("command-receiver", receiver_loop, args)
        return False

    def start_live_receiver(self, *args):
        """Legacy compatibility: start live data receiver thread."""
        if len(args) >= 4:
            def live_loop():
                # Implement live receiver logic or delegate to legacy
                pass
            return self.start_thread("live-receiver", live_loop, args)
        return False

    def start_sender(self, *args):
        """Legacy compatibility: start command sender thread."""
        if len(args) >= 4:
            def sender_loop():
                # Implement sender logic or delegate to legacy
                pass
            return self.start_thread("sender", sender_loop, args)
        return False

    def start_processing(self, *args):
        """Legacy compatibility: start data processing thread."""
        if len(args) >= 4:
            def processing_loop():
                # Implement processing logic or delegate to legacy
                pass
            return self.start_thread("processor", processing_loop, args)
        return False