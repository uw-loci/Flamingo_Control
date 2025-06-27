# TODO probably rename this file. There will be another module for connecting to the microscope, this is more helper functions for that
import os
import socket
import time
import tkinter as tk
from threading import Event, Thread
from tkinter import messagebox
from typing import Sequence

from pathlib import Path
from py2flamingo.functions.text_file_parsing import text_to_dict
from py2flamingo.functions.threads import (
    command_listen_thread,
    live_listen_thread,
    processing_thread,
    send_thread,
)

# Default values for LED on and off
LED_off = "00.00 0"
LED_on = "50.0 1"
# commands
funcs = Path(__file__).parent

commands = text_to_dict(Path(__file__).parent / "command_list.txt")

COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD = int(
    commands["CommandCodes.h"]["COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD"]
)
COMMAND_CODES_COMMON_SCOPE_SETTINGS = int(
    commands["CommandCodes.h"]["COMMAND_CODES_COMMON_SCOPE_SETTINGS"]
)
COMMAND_CODES_CAMERA_WORK_FLOW_START = int(
    commands["CommandCodes.h"]["COMMAND_CODES_CAMERA_WORK_FLOW_START"]
)
COMMAND_CODES_STAGE_POSITION_SET = int(
    commands["CommandCodes.h"]["COMMAND_CODES_STAGE_POSITION_SET"]
)
COMMAND_CODES_SYSTEM_STATE_IDLE = int(
    commands["CommandCodes.h"]["COMMAND_CODES_SYSTEM_STATE_IDLE"]
)
COMMAND_CODES_CAMERA_PIXEL_FIELD_Of_VIEW_GET = int(
    commands["CommandCodes.h"]["COMMAND_CODES_CAMERA_PIXEL_FIELD_Of_VIEW_GET"]
)
COMMAND_CODES_CAMERA_IMAGE_SIZE_GET = int(
    commands["CommandCodes.h"]["COMMAND_CODES_CAMERA_IMAGE_SIZE_GET"]
)
COMMAND_CODES_SYSTEM_STATE_GET = int(
    commands["CommandCodes.h"]["COMMAND_CODES_SYSTEM_STATE_GET"]
)
COMMAND_CODES_CAMERA_WORK_FLOW_STOP = int(
    commands["CommandCodes.h"]["COMMAND_CODES_CAMERA_WORK_FLOW_STOP"]
)
##############################


def move_axis(command_data_queue, command_queue, send_event, axis_code, value):
    """
    Move a specific axis to the specified value.

    Parameters
    ----------
    command_data_queue : queue.Queue
        The queue to store the command data.
    command_queue : queue.Queue
        The queue to store the command.
    send_event : threading.Event
        The event to trigger sending of commands.
    axis_code : int
        The code of the axis to move.
    value : float
        The value to move the axis to.

    Returns
    -------
    None
    """
    command_data_queue.put([axis_code, 0, 0, value])
    command_queue.put(COMMAND_CODES_STAGE_POSITION_SET)
    send_event.set()
    while not command_queue.empty():
        time.sleep(0.1)


def go_to_XYZR(command_data_queue, command_queue, send_event, xyzr: Sequence[float]):
    """
    Move to the specified XYZR coordinates.

    Parameters
    ----------
    command_data_queue : queue.Queue
        The queue to store the command data.
    command_queue : queue.Queue
        The queue to store the command.
    send_event : threading.Event
        The event to trigger sending of commands.
    xyzr : Sequence[float]
        The XYZR coordinates to move to. r is in degrees, other values are in mm.

    Returns
    -------
    None
    """
    # Unpack the provided XYZR coordinates, r is in degrees, other values are in mm
    x, y, z, r = xyzr
    print(f"Moving to {x} {y} {z} {r}")

    move_axis(command_data_queue, command_queue, send_event, 1, x)  # X-axis
    move_axis(command_data_queue, command_queue, send_event, 3, z)  # Z-axis
    move_axis(command_data_queue, command_queue, send_event, 4, r)  # Rotation
    move_axis(command_data_queue, command_queue, send_event, 2, y)  # Y-axis


def get_microscope_settings(command_queue, other_data_queue, send_event):
    """
    Retrieve microscope settings and image pixel size.

    This function retrieves the microscope settings and image pixel size by sending relevant commands to the controller.

    Parameters
    ----------
    command_queue : queue.Queue
        The queue to store the command.
    other_data_queue : queue.Queue
        The queue to store other data.
    send_event : threading.Event
        The event to trigger sending of commands.

    Returns
    -------
    Tuple[float, dict]
        A tuple containing the image pixel size and the microscope settings dictionary.
    """
    send_command(command_queue, COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD, send_event)
    # Microscope settings should now be in a text file called ScopeSettings.txt in the 'microscope_settings' directory.
    # Convert them into a dictionary to extract useful information.
    scope_settings = text_to_dict("microscope_settings/ScopeSettings.txt")

    send_command(
        command_queue, COMMAND_CODES_CAMERA_PIXEL_FIELD_Of_VIEW_GET, send_event
    )
    tube_Lens_Length = float(
        scope_settings["Type"]["Tube lens design focal length (mm)"]
    )
    tube_Lens_Design_FocalLength = float(
        scope_settings["Type"]["Tube lens length (mm)"]
    )
    objective_Lens_Magnification = float(
        scope_settings["Type"]["Objective lens magnification"]
    )
    # Get the image pixel size from the other data queue
    image_pixel_size = other_data_queue.get()
    # image_pixel_size = camera_pixel_size / ((tube_Lens_Length / tube_Lens_Design_FocalLength) * objective_Lens_Magnification)
    print(
        f"image pixel size from system {image_pixel_size}, objective mag {objective_Lens_Magnification}"
    )
    # return 0.000488, scope_settings
    return image_pixel_size, scope_settings


def send_command(command_queue, arg1, send_event):
    command_queue.put(arg1)
    send_event.set()
    while not command_queue.empty():
        time.sleep(0.3)


def start_connection(NUC_IP: str, PORT_NUC: int):
    """
    Start the connection with the Flamingo NUC and live client.

    This function establishes the connection with the Flamingo NUC and live client using the provided IP and port.

    Parameters
    ----------
    NUC_IP : str
        The IP address of the Flamingo NUC.
    PORT_NUC : int
        The port number for the NUC connection.

    Returns
    -------
    Tuple[socket.socket, socket.socket, str, str]
        A tuple containing the NUC client socket, live client socket, ZStack.txt filename, LED on state, and LED off state.
    """
    PORT_LISTEN = PORT_NUC + 1
    wf_zstack = "ZStack.txt"  # Fluorescent Z stack to find sample

    try:
        # Create and connect the NUC client socket
        nuc_client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        nuc_client.settimeout(2)
        nuc_client.connect((NUC_IP, PORT_NUC))

        # Create and connect the live client socket
        live_client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        live_client.connect((NUC_IP, PORT_LISTEN))
    except (socket.timeout, ConnectionRefusedError) as e:
        # Handle the connection error and show a popup message
        root = tk.Tk()
        root.withdraw()
        if isinstance(e, socket.timeout):
            message = "Check that you have network access to the microscope. This may also be an IT issue, or the software may have crashed on the microscope control side (Linux). Close this program and try again."
        else:
            message = "Connection was refused."
        messagebox.showinfo("Connection Error", message)
        exit()

    # Return the NUC client socket, live client socket, ZStack.txt filename, LED on state, and LED off state
    return nuc_client, live_client, wf_zstack, LED_on, LED_off


def create_threads(
    nuc_client: socket,
    live_client: socket,
    other_data_queue=None,
    image_queue=None,
    command_queue=None,
    z_plane_queue=None,
    intensity_queue=None,
    visualize_queue=None,
    system_idle=None,
    processing_event=None,
    send_event=None,
    terminate_event=None,
    command_data_queue=None,
    stage_location_queue=None,
):
    # Create the image processing thread
    processing_thread_var = Thread(
        target=processing_thread,
        args=(
            z_plane_queue,
            terminate_event,
            processing_event,
            intensity_queue,
            image_queue,
        ),
    )

    # Create the send thread to send individual commands and workflows to the microscope control software
    send_thread_var = Thread(
        target=send_thread,
        args=(nuc_client, command_queue, send_event, system_idle, command_data_queue),
    )

    # Create the command listen thread to listen to responses from the microscope about its status
    command_listen_thread_var = Thread(
        target=command_listen_thread,
        args=(nuc_client, system_idle, terminate_event, other_data_queue),
    )

    # Create the live listen thread to receive image data sent to the 'live' view
    live_listen_thread_var = Thread(
        target=live_listen_thread,
        args=(live_client, terminate_event, image_queue, visualize_queue),
    )

    # Set daemon flag for threads (optional)
    live_listen_thread_var.daemon = True
    send_thread_var.daemon = True
    command_listen_thread_var.daemon = True
    processing_thread_var.daemon = True

    # Start the threads
    live_listen_thread_var.start()
    command_listen_thread_var.start()
    send_thread_var.start()
    processing_thread_var.start()

    # Return the thread variables for potential later use
    return (
        live_listen_thread_var,
        command_listen_thread_var,
        send_thread_var,
        processing_thread_var,
    )


def close_connection(
    nuc_client: socket,
    live_client: socket,
    live_listen_thread_var: Thread,
    command_listen_thread_var: Thread,
    send_thread_var: Thread,
    processing_thread_var: Thread,
):
    """
    Close the connection with the microscope and terminate the threads.

    This function joins the threads to ensure they finish their tasks, closes the socket connections,
    and terminates the threads.

    Parameters
    ----------
    nuc_client : socket
        The socket for communication with the NUC.
    live_client : socket
        The socket for receiving live image data.
    live_listen_thread_var : Thread
        The variable representing the live listen thread.
    command_listen_thread_var : Thread
        The variable representing the command listen thread.
    send_thread_var : Thread
        The variable representing the send thread.
    processing_thread_var : Thread
        The variable representing the processing thread.
    """
    # Join the threads to ensure they finish their tasks
    send_thread_var.join()
    live_listen_thread_var.join()
    command_listen_thread_var.join()
    processing_thread_var.join()

    # Close the socket connections
    nuc_client.close()
    live_client.close()
