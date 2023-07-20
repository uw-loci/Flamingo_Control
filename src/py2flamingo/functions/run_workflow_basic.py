import os
import shutil
import time
from typing import Sequence

from functions.text_file_parsing import *
from global_objects import clear_all_events_queues

plane_spacing = 10
framerate = 40.0032  # /s


def run_workflow(
    connection_data,
    sample_name,
    workflow_dict,
    visualize_event,
    image_queue,
    command_queue,
    stage_location_queue,
    send_event,
):
    # Load command codes from the command_list.txt file
    commands = text_to_dict(
        os.path.join("src", "py2flamingo", "functions", "command_list.txt")
    )
    COMMAND_CODES_CAMERA_WORK_FLOW_START = int(
        commands["CommandCodes.h"]["COMMAND_CODES_CAMERA_WORK_FLOW_START"]
    )
    print("Running workflow")

    # Clear all events and queues to ensure a clean start
    clear_all_events_queues()

    wf_dict = dict_comment(workflow_dict, "Volume test")
    wf_dict = dict_save_directory(wf_dict, directory="Volume test")
    dict_to_workflow(os.path.join("workflows", "testvolume.txt"), wf_dict)

    # Copy the currentSnapshot.txt file to the workflow.txt file
    shutil.copy(
        os.path.join("workflows", "testvolume.txt"),
        os.path.join("workflows", "workflow.txt"),
    )

    # Send the command to start the workflow to the microscope
    command_queue.put(COMMAND_CODES_CAMERA_WORK_FLOW_START)
    send_event.set()
    # Put the xyzr coordinates into the stage_location_queue and set the visualize_event
    stage_location_queue.put([-1, -1, -1, -1])
    visualize_event.set()

    # Retrieve the image from the image_queue
    image_queue.get()

    print("Data Acquired.")
