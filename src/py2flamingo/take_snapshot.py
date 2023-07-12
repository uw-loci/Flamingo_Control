import os
import shutil
import time
from typing import Sequence
import functions.microscope_interactions as scope
from functions.text_file_parsing import *
from global_objects import clear_all_events_queues

plane_spacing = 10
framerate = 40.0032  # /s


def take_snapshot(
    connection_data: Sequence,
    xyzr_init: Sequence[float],
    visualize_event,
    other_data_queue,
    image_queue,
    command_queue,
    stage_location_queue,
    send_event,
    laser_channel="Laser 3 488 nm",
    laser_setting="5.00 1",
):
    """
    Takes a snapshot of the current view of the microscope.

    Parameters:
    connection_data (list): List containing connection data.
    xyzr_init (Sequence[float]): Initial xyzr coordinates for the microscope.
    visualize_event (Event): Event to indicate when there is an image to visualize.
    image_queue (Queue): Queue to hold the image data.
    command_queue (Queue): Queue to hold the commands to be sent to the microscope.
    stage_location_queue (Queue): Queue to hold the stage location data.
    send_event (Event): Event to indicate when a command should be sent to the microscope.
    laser_channel (str, optional): Laser channel to use for the snapshot. Defaults to "Laser 3 488 nm".
    laser_setting (str, optional): Laser setting to use for the snapshot. Defaults to "5.00 1".

    Returns:
    None
    """
    print("Taking snapshot")

    # Extract the workflow zstack from the connection data
    wf_zstack = connection_data[2]

    # Clear all events and queues to ensure a clean start
    clear_all_events_queues()

    # Load command codes from the command_list.txt file
    commands = text_to_dict(
        os.path.join("src", "py2flamingo", "functions", "command_list.txt")
    )
    COMMAND_CODES_CAMERA_WORK_FLOW_START = int(
        commands["CommandCodes.h"]["COMMAND_CODES_CAMERA_WORK_FLOW_START"]
    )
    COMMAND_CODES_CAMERA_CHECK_STACK = int(
        commands["CommandCodes.h"]["COMMAND_CODES_CAMERA_CHECK_STACK"]
    )

    # Prepare the workflow for the snapshot
    snap_dict = workflow_to_dict(os.path.join("workflows", wf_zstack))
    snap_dict = dict_to_snap(snap_dict, xyzr_init, framerate, plane_spacing)
    snap_dict = laser_or_LED(snap_dict, laser_channel, laser_setting, laser_on=True)
    snap_dict = dict_comment(snap_dict, "GUI Snapshot")
    snap_dict = dict_save_directory(snap_dict, directory="Snapshots")
    # Write the updated workflow back to the currentSnapshot.txt file
    dict_to_workflow(os.path.join("workflows", "currentSnapshot.txt"), snap_dict)
    # Copy the currentSnapshot.txt file to the workflow.txt file
    shutil.copy(
        os.path.join("workflows", "currentSnapshot.txt"),
        os.path.join("workflows", "workflow.txt"),
    )
    scope.check_workflow(command_queue, send_event, other_data_queue, COMMAND_CODES_CAMERA_CHECK_STACK)
    # Send the command to start the workflow to the microscope

    command_queue.put(COMMAND_CODES_CAMERA_WORK_FLOW_START)
    send_event.set()

    # Put the xyzr coordinates into the stage_location_queue and set the visualize_event
    stage_location_queue.put(xyzr_init)
    visualize_event.set()

    # Retrieve the image from the image_queue
    image_data = image_queue.get()

    print("snapshot taken")
    # TODO Clean up 'delete' PNG files or dont make them
    return image_data