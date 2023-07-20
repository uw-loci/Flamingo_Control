# Functions that run in parallel with the main script, listening for data, sending commands/workflows, and performing processing steps
import os
import select
import socket
import struct
import time
from threading import Event
from typing import Sequence

import functions.calculations
import functions.tcpip_nuc
import numpy as np
from functions.text_file_parsing import text_to_dict, workflow_to_dict
from PIL import Image
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QImage, QPixmap
from PyQt5.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

index = 0
commands = text_to_dict(
    os.path.join("src", "py2flamingo", "functions", "command_list.txt")
)

COMMAND_CODES_COMMON_SCOPE_SETTINGS_SAVE = int(
    commands["CommandCodes.h"]["COMMAND_CODES_COMMON_SCOPE_SETTINGS_SAVE"]
)
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
COMMAND_CODES_CAMERA_CHECK_STACK = int(
    commands["CommandCodes.h"]["COMMAND_CODES_CAMERA_CHECK_STACK"]
)
COMMAND_CODES_CAMERA_CHECK_STACK_UPDATES = int(
    commands["CommandCodes.h"]["COMMAND_CODES_CAMERA_CHECK_STACK_UPDATES"]
)
##############################


def clear_socket(sock: socket):
    """
    Empty the data present on the socket.

    This function reads all data available on the socket and discards it.
    It's useful for ensuring a clean start before sending or receiving new data.

    Parameters
    ----------
    sock: socket
        The socket to be cleared of data.
    """
    # Create a list with the socket as its only member
    input_data = [sock]

    while True:
        # Set socket to non-blocking mode
        sock.setblocking(0)

        # Use select to check if there is data available on the socket
        inputready, _, _ = select.select(input_data, [], [], 0.0)

        print(f"Data amount waiting {len(inputready)}")

        # If there is no data available, break the loop
        if len(inputready) == 0:
            break

        # If there is data available, receive it and discard
        for s in inputready:
            s.recv(1)

    print(f"socket {sock} clear")

    # Set socket back to blocking mode
    sock.setblocking(1)


def bytes_waiting(sock: socket):
    """
    Check if there is data waiting to be read on the socket and return the number of bytes.

    Parameters
    ----------
    sock: socket
        The socket to be checked for waiting data.

    Returns
    -------
    int
        The number of bytes waiting to be read. If there is no data waiting, return 0.
    """
    # Use select() to check if there is data waiting to be read on the socket
    r, _, _ = select.select([sock], [], [], 0)

    if r:
        # If there is data waiting, set socket to non-blocking mode
        sock.setblocking(0)

        # Peek at the first byte of data and get the total number of bytes waiting to be read
        data = len(sock.recv(80000, socket.MSG_PEEK))

        # Set socket back to blocking mode
        sock.setblocking(1)

        # Return the length of data
        return data
    else:
        # If there is no data waiting, return 0
        return 0


# Commands sent to the Nuc get responses, this listens for and processes those responses.
#########################COMMAND_LISTENING SECTION#############


def unpack_received_message(msg):
    """
    Unpacks the received message using a specific struct format.

    Parameters
    ----------
    msg : bytes
        The received message to unpack.

    Returns
    -------
    tuple
        The unpacked values from the message.
    """
    # Unpack the received message
    s = struct.Struct("I I I I I I I I I I d I 72s I")
    return s.unpack(msg)


def handle_idle_state(received, idle_state):
    """
    Handles the idle state based on the received message.

    Parameters
    ----------
    received : tuple
        The unpacked values from the received message.
    idle_state : threading.Event
        The event to set when the system is idle.

    Returns
    -------
    None
    """
    print(f"status idle: {str(received[2])}")
    if received[2] == 1:
        idle_state.set()


def fetch_microscope_settings(received, client):
    """
    Fetches the microscope settings from the client.

    Parameters
    ----------
    received : tuple
        The unpacked values from the received message.
    client : socket
        The socket client to fetch the settings from.

    Returns
    -------
    None
    """
    time.sleep(0.05)
    print(f"Getting microscope settings = {received[2]}")

    # Fetch the microscope settings
    bytes_data = bytes_waiting(client)
    text_bytes = client.recv(bytes_data)

    # Save the settings to a file
    if not os.path.exists("microscope_settings"):
        os.makedirs("microscope_settings")
    file_path = os.path.join("microscope_settings", "ScopeSettings.txt")

    # If the file already exists, remove it
    if os.path.exists(file_path):
        os.remove(file_path)

    # Write the new data to the file
    with open(file_path, "wb") as file:
        file.write(text_bytes)


def pixel_field_of_view(received, other_data_queue):
    """
    Handles the pixel field of view based on the received message.

    Parameters
    ----------
    received : tuple
        The unpacked values from the received message.
    other_data_queue : queue.Queue
        The queue to put the pixel field of view value.

    Returns
    -------
    None
    """
    # print("pixel size " + str(received[10]))
    if received[10] < 0:
        print(
            "Threads.py command_listen_thread: No pixel size detected from system. Exiting."
        )
        exit()
    other_data_queue.put(received[10])


def check_stack(other_data_queue, client):
    # print('Check stack output')
    # for value in received:
    #     print(value)
    # print(f"Checking Stack Viability = {received[2]}")
    bytes_data = bytes_waiting(client)
    text_bytes = client.recv(bytes_data)
    other_data_queue.put(text_bytes)
    # Possibly replace with adding the text data to other_data_queue


def handle_camera_frame_size(received, other_data_queue):
    """
    Handles the camera frame size based on the received message.

    Parameters
    ----------
    received : tuple
        The unpacked values from the received message.
    other_data_queue : queue.Queue
        The queue to put the camera frame size value.

    Returns
    -------
    None
    """
    # print("frame size " + str(received[7]))
    if received[10] < 0:
        print(
            "Threads.py command_listen_thread: No camera size detected from system. Exiting."
        )
        exit()
    other_data_queue.put(received[7])


def command_listen_thread(
    client: socket, idle_state: Event, terminate_event: Event, other_data_queue
):
    """
    Thread that listens for commands from the client and handles them accordingly.

    Parameters
    ----------
    client : socket
        The socket client to listen for commands.
    idle_state : threading.Event
        The event to set when the system is idle.
    terminate_event : threading.Event
        The event to terminate the thread.
    other_data_queue : queue.Queue
        The queue to store other data.

    Returns
    -------
    None
    """
    print(f"LISTENING for commands on {str(client)}")

    # Clear out any data currently in the socket
    clear_socket(client)

    while not terminate_event.is_set():
        # Wait to receive a message from the client
        # print('waiting for command response')
        msg = client.recv(128)

        # Ignore messages of incorrect size
        if len(msg) != 128:
            continue

        # Unpack the received message
        received = unpack_received_message(msg)
        # print('listening to 53717 got ' + str(received[1]))
        # Check the command code in the received message and respond appropriately
        if received[1] == COMMAND_CODES_SYSTEM_STATE_IDLE:
            handle_idle_state(received, idle_state)
        elif received[1] == COMMAND_CODES_COMMON_SCOPE_SETTINGS:
            fetch_microscope_settings(received, client)
        elif received[1] == COMMAND_CODES_CAMERA_PIXEL_FIELD_Of_VIEW_GET:
            pixel_field_of_view(received, other_data_queue)
        elif received[1] == COMMAND_CODES_CAMERA_CHECK_STACK:
            check_stack(other_data_queue, client)
        elif received[1] == COMMAND_CODES_CAMERA_IMAGE_SIZE_GET:
            handle_camera_frame_size(received, other_data_queue)


##################################################


############LIVE (IMAGE) LISTEN THREAD SECTION################
def receive_image_data(live_client, image_size):
    """
    Receives image data from the live client socket.

    This function receives the image data in chunks from the live client socket until the entire image
    has been received.

    Parameters
    ----------
    live_client : socket
        The live client socket for receiving data.

    image_size : int
        The size of the image data to be received.

    Returns
    -------
    image_data : bytes
        The received image data as bytes.

    Raises
    ------
    socket.error
        If incomplete image data is received.

    """
    image_data = b""
    while len(image_data) < image_size:
        if data := live_client.recv(image_size - len(image_data)):
            image_data += data
        else:
            raise socket.error("Incomplete image data")
    return image_data


def process_single_image(
    live_client, image_size, image_width, image_height, image_queue, visualize_queue
):
    """
    Processes a single image received from the live client socket.

    This function receives a single image from the live client socket, processes it, and puts it into
    the image queue and visualize queue for further use.

    Parameters
    ----------
    live_client : socket
        The live client socket for receiving data.

    image_size : int
        The size of the image data to be received.

    image_width : int
        The width of the image.

    image_height : int
        The height of the image.

    image_queue : Queue
        A queue for holding the received image data.

    visualize_queue : Queue
        A queue for holding the image data to be used for visualization.

    """
    # image_data starts as a byte stream
    image_data = receive_image_data(live_client, image_size)
    # build and reformat pixel data
    image_array = np.frombuffer(image_data, dtype=np.uint16)
    # TODO move out of image processing?
    image_array = image_array.reshape((image_height, image_width)).T
    image_array = np.flipud(image_array)
    # print("putting image into queues")
    image_queue.put(np.array(image_array))
    visualize_queue.put(np.array(image_array))


def receive_zstack_images(
    live_client, image_size, image_width, image_height, stack_size, Zpos, image_queue
):
    """
    Receives a stack of images from the live client socket.

    This function receives a stack of images from the live client socket, rotates and saves each image,
    and combines them into a 3D array. The resulting stack is then put into the image queue for further use.

    Parameters
    ----------
    live_client : socket
        The live client socket for receiving data.

    image_size : int
        The size in bytes of each image in the stack.

    image_width : int
        The width of the images in pixels.

    image_height : int
        The height of the images in pixzels.

    stack_size : int
        The number of images in the stack.

    Zpos : float
        The Z position of an image.

    image_queue : Queue
        A queue for holding the received image stack.

    """
    images = []
    live_client.settimeout(1)
    exit_loop = False
    step = 0

    while step < stack_size:
        if exit_loop:
            print(
                "1 second timeout reached while waiting for additional Z slices, returning to standard listening mode"
            )
            break

        image_data = receive_image_data(live_client, image_size)

        image_array = np.frombuffer(image_data, dtype=np.uint16)
        # TODO move out of image processing?
        image_array = image_array.reshape((image_height, image_width)).T
        image_array = np.flipud(image_array)

        images.append(image_array)
        step += 1

        if step != stack_size:
            try:
                header_data = live_client.recv(40)
            except socket.timeout:
                exit_loop = True

    stack = np.stack([np.array(image) for image in images])
    live_client.settimeout(0)
    live_client.setblocking(True)

    image_queue.put(stack)


def live_listen_thread(
    live_client: socket, terminate_event: Event, image_queue, visualize_queue
):
    """
    Thread for listening to image data from the live client socket.

    This thread listens to the live client socket for incoming image data. It receives the image
    header, parses it, and determines whether it's a single image or a stack of images based on the
    workflow settings. It then processes the received data accordingly.

    Parameters
    ----------
    live_client : socket
        The live client socket for receiving image data.

    terminate_event : Event
        An event to signal when to terminate the thread.

    image_queue : Queue
        A queue for holding the received image data.

    visualize_queue : Queue
        A queue for holding the image data to be used for visualization.

    """
    global index
    print(f"LISTENING for image data on {str(live_client)}")

    while True:
        try:
            header_data = live_client.recv(40)
        except socket.error as e:
            print(f"Socket error on image data listener: {e}")
            live_client.close()
            break

        if len(header_data) != 40:
            raise ValueError(
                f"Header length should be 40 bytes, not {len(header_data)}"
            )
        # parse the header
        # print('parsing header, entering data acquisition')
        header = struct.unpack("I I I I I I I I I I", header_data)
        # Check which camera/objective is being used.
        # print(f'hardwareID number: {header[3]}')
        image_size, image_width, image_height = header[0], header[1], header[2]
        # get the stack size and other info from the workflow file, as it is not sent as part of the header information
        current_workflow_dict = workflow_to_dict(
            os.path.join("workflows", "workflow.txt")
        )
        if isinstance(current_workflow_dict, list):
            current_workflow_dict = current_workflow_dict[0]

        temp = current_workflow_dict["Stack Settings"]["Number of planes"]
        if temp != "auto":
            stack_size = float(
                current_workflow_dict["Stack Settings"]["Number of planes"]
            )
        else:
            stack_size = 200
        MIP = current_workflow_dict["Experiment Settings"]["Display max projection"]
        if MIP == "true" or stack_size == 1:
            process_single_image(
                live_client,
                image_size,
                image_width,
                image_height,
                image_queue,
                visualize_queue,
            )
        else:
            # name = current_workflow_dict["Experiment Settings"]["Comments"]
            Zpos = current_workflow_dict["Start Position"]["Z (mm)"]

            receive_zstack_images(
                live_client,
                image_size,
                image_width,
                image_height,
                stack_size,
                Zpos,
                image_queue,
            )

    print("Image data collection thread terminating")
    return


######################################################


####################SEND COMMANDS THREAD SECTION################
def handle_workflow_start(client):
    """
    Handles the command to start a workflow in the Flamingo controller.

    Parameters
    ----------
    client : socket
        The socket client for communication.

    Returns
    -------
    None
    """
    # print('Workflow sent')
    functions.tcpip_nuc.text_to_nuc(
        client,
        os.path.join("workflows", "workflow.txt"),
        COMMAND_CODES_CAMERA_WORK_FLOW_START,
    )


def handle_scope_settings_save(client):
    """
    Handles the command to save microscope settings for the home position in the Flamingo controller.

    Parameters
    ----------
    client : socket
        The socket client for communication.

    Returns
    -------
    None
    """
    print("Saving microscope settings for home position")
    functions.tcpip_nuc.text_to_nuc(
        client,
        os.path.join("microscope_settings", "send_settings.txt"),
        COMMAND_CODES_COMMON_SCOPE_SETTINGS_SAVE,
    )


def check_workflow(client):
    # print('Checking workflow for errors')
    functions.tcpip_nuc.text_to_nuc(
        client,
        os.path.join("workflows", "workflow.txt"),
        COMMAND_CODES_CAMERA_CHECK_STACK,
    )


def handle_non_workflow_command(client, command: int, command_data: Sequence):
    """
    Handles a non-workflow command in the Flamingo controller.

    Parameters
    ----------
    client : socket
        The socket client for communication.
    command : int
        The command code.
    command_data : list
        Additional command data.

    Returns
    -------
    None
    """
    if command_data:
        functions.tcpip_nuc.command_to_nuc(client, command, command_data)
    else:
        functions.tcpip_nuc.command_to_nuc(client, command)


def send_thread(
    client: socket, command_queue, send_event, system_idle: Event, command_data_queue
):
    """
    Thread that sends commands or workflows to the Flamingo controller.

    Parameters
    ----------
    client : socket
        The socket client for communication.
    command_queue : queue.Queue
        The queue containing commands to be sent.
    send_event : threading.Event
        The event that triggers sending of commands.
    system_idle : threading.Event
        The event that indicates the system is idle.
    command_data_queue : queue.Queue
        The queue containing additional command data.

    Returns
    -------
    None
    """
    while True:
        # Wait for the send event to be set
        send_event.wait()

        command = command_queue.get()
        system_idle.clear()

        # Handle workflows separately (special type of command)
        if command == COMMAND_CODES_CAMERA_WORK_FLOW_START:
            handle_workflow_start(client)
        elif command == COMMAND_CODES_CAMERA_CHECK_STACK:
            check_workflow(client)
        elif command == COMMAND_CODES_COMMON_SCOPE_SETTINGS_SAVE:
            handle_scope_settings_save(client)
        else:  # Handle all other commands
            # print("Send non-workflow command to nuc: " + str(command))
            command_data = []

            if not command_data_queue.empty():
                command_data = command_data_queue.get()

            handle_non_workflow_command(client, command, command_data)

        send_event.clear()


################################################


##Needs the most work, and will probably get the most expansion in the near future.
def processing_thread(
    z_plane_queue, terminate_event, processing_event, intensity_queue, image_queue
):
    """
    Thread for processing data from the image queue.

    This thread waits for the processing event to be set, indicating that there is data available
    in the image queue to be processed. It retrieves the data from the image queue and performs
    specific processing based on the type of data received. The processed results are then stored
    in the appropriate output queues.

    Parameters
    ----------
    z_plane_queue : Queue
        A queue for holding the processed data related to the most in-focus plane.

    terminate_event : Event
        An event to signal when to terminate the thread.

    processing_event : Event
        An event to signal when there is data available for processing.

    intensity_queue : Queue
        A queue for holding the calculated mean intensity values.

    image_queue : Queue
        A queue for holding the image data to be processed.

    """
    while True:
        processing_event.wait()
        processing_event.clear()
        # print("waiting for image")
        # image_data = image_queue.get()
        # #print("Processing image")
        # if len(image_data.shape) == 2:
        #     print('processing 2D')
        #     #depending on the need for granularity, could either use the mean of the brightest parts of the entire image
        #     #or find the brightest lines using a rolling average
        #     mean_largest_quarter, y_intensity_map= functions.calculations.calculate_rolling_y_intensity(image_data, 21)

        #     intensity_queue.put(y_intensity_map)
        #     processing_event.clear()
        # else:
        #     print('processig 3D')
        #     z_plane_queue.put(
        #         functions.calculations.find_most_in_focus_plane(image_data)
        #     )
        #     processing_event.clear()
