import os
import time
from queue import Queue
from threading import Event
from functions.text_file_parsing import *
from typing import Sequence

def set_home(
    connection_data: Sequence,
    xyzr: Sequence[float],
    command_queue: Queue,
    other_data_queue: Queue,
    send_event: Event,
):
    """
    Sets the home coordinates for the microscope's stage.

    This function sends commands to the microscope to load its current settings. It then modifies these settings to
    update the home coordinates and save these new settings back to the microscope. 

    Parameters
    ----------
    connection_data: list
        List containing client objects and functions related to the microscope connection.
    xyzr: list
        List containing the desired home coordinates for the x, y, z, and r axes.
    command_queue: Queue
        A queue for storing commands to be sent to the microscope.
    other_data_queue: Queue
        A queue for storing other types of data.
    send_event: Event
        An event that signals when data should be sent to the microscope.
    """
    # Unpack the connection_data list
    nuc_client, live_client, wf_zstack, LED_on, LED_off = connection_data

    # Load command list from text file and convert it to a dictionary
    commands = text_to_dict(os.path.join("src", "py2flamingo", "functions", "command_list.txt"))

    # Load commands for loading and saving scope settings from the dictionary
    COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD = int(commands["CommandCodes.h"]["COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD"])
    COMMAND_CODES_COMMON_SCOPE_SETTINGS_SAVE = int(commands["CommandCodes.h"]["COMMAND_CODES_COMMON_SCOPE_SETTINGS_SAVE"])

    print("load settings")
    print(COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD)

    # Get microscope settings file to temp location
    command_queue.put(COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD)  # Movement command
    send_event.set()
    while not command_queue.empty():
        time.sleep(0.3)  # Wait for queue to be processed

    # Microscope settings should now be in a text file called ScopeSettings.txt in the 'workflows' directory
    # Convert them into a dictionary to extract useful information
    settings_dict = text_to_dict(os.path.join("microscope_settings", "ScopeSettings.txt"))

    # Update the home coordinates in the settings dictionary
    settings_dict["Stage limits"]["Home x-axis"] = xyzr[0]
    settings_dict["Stage limits"]["Home y-axis"] = xyzr[1]
    settings_dict["Stage limits"]["Home z-axis"] = xyzr[2]
    settings_dict["Stage limits"]["Home r-axis"] = xyzr[3]

    # Convert the updated settings dictionary back into a text file
    dict_to_text(os.path.join("microscope_settings", "send_settings.txt"), settings_dict)

    print("save settings")

    # Send command to microscope to save the updated settings
    command_queue.put(COMMAND_CODES_COMMON_SCOPE_SETTINGS_SAVE)
    send_event.set()

    # Allow time for command to be processed
    time.sleep(0.2)

    # Define the file path
    file_path = os.path.join("microscope_settings", "send_settings.txt")

    # Check if the file exists before trying to delete it
    if os.path.isfile(file_path):
        os.remove(file_path)
    else:
        print(f"{file_path} not found.")
