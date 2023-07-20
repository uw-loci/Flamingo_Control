# TODO: Ask Joe about directory structure when writing folders for save image directory

import copy
import os
import shutil

import functions.calculations as calc
import functions.microscope_connect as mc
import functions.microscope_interactions as scope
import functions.text_file_parsing as txt
import numpy as np
from functions.image_display import save_png
from take_snapshot import take_snapshot

FRAMERATE = 40.0032  # /s


def multi_angle_collection(
    connection_data,
    visualize_event,
    other_data_queue,
    image_queue,
    command_queue,
    command_data_queue,
    system_idle,
    processing_event,
    send_event,
    terminate_event,
    stage_location_queue,
    angle_step_size_deg,
    sample_name,
    comment,
    top_points,
    bottom_points,
    workflow_filename,
    # overlap_percent,
):
    """
    Goal: Take in a workflow file, an angle increment, and a set of bounding points. Collect volumes at each angle that encompass the entire sample.
    """
    # Gather some useful information

    _, _, wf_zstack, _, _ = connection_data
    command_labels, _, _, image_pixel_size_mm, frame_size = scope.initial_setup(
        command_queue, other_data_queue, send_event
    )
    (
        COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD,
        COMMAND_CODES_CAMERA_WORK_FLOW_START,
        COMMAND_CODES_STAGE_POSITION_SET,
        COMMAND_CODES_CAMERA_PIXEL_FIELD_Of_VIEW_GET,
        COMMAND_CODES_CAMERA_IMAGE_SIZE_GET,
        COMMAND_CODES_CAMERA_CHECK_STACK,
    ) = command_labels
    wf_dict = txt.workflow_to_dict(os.path.join("workflows", workflow_filename))
    # parent_save_dir = os.path.join(wf_dict["Experiment Settings"]["Save image directory"], sample_name)
    # wf_dict["Experiment Settings"]["Save image directory"] = parent_save_dir
    # if not os.path.exists(parent_save_dir):
    # os.makedirs(parent_save_dir)
    wf_dict["Experiment Settings"]["Save image drive"] = os.path.join(
        wf_dict["Experiment Settings"]["Save image drive"], sample_name
    ).replace("\\", "/")
    # Set the workflow to the appropriate type, in this case we want a tile to get a full volume
    wf_dict = txt.set_workflow_type(wf_dict, "Tile", overlap=10)
    wf_dict = txt.dict_comment(wf_dict, comment)
    # iterate through all possible angles and run the workflow at interpolated bounding coordinates for each.
    for i in range(int(360 / angle_step_size_deg)):
        print(f"collecting: {i*angle_step_size_deg}")
        xyzr_top = calc.bounding_point_from_angle(top_points, angle_step_size_deg * i)
        xyzr_bottom = calc.bounding_point_from_angle(
            bottom_points, angle_step_size_deg * i
        )
        center_point = calc.find_center(
            xyzr_top, xyzr_bottom, shift=frame_size * image_pixel_size_mm / 2
        )
        wf_dict = txt.dict_positions(
            wf_dict, xyzr_top, xyzr_bottom, save_with_data=True,
        )

        z_range = abs(float(xyzr_top[2]) - float(xyzr_bottom[2]))
        current_angle = xyzr_top[3]
        wf_dict = txt.calculate_zplanes(wf_dict, z_range, FRAMERATE)
        # WARNING: Tradeoff between not having decimal and potentially causing duplications if sub single percent angles are selected.
        # .replace needed since os.join writes for a Windows system on Windows, which doesn't work when sent to Linux.
        # wf_dict["Experiment Settings"]["Save image directory"] = os.path.join(
        #     sample_name, f"{sample_name}_{int(current_angle)}"
        # ).replace('\\', '/')
        wf_dict["Experiment Settings"][
            "Save image directory"
        ] = f"{sample_name}_{int(current_angle)}"
        txt.dict_to_workflow(os.path.join("workflows", f"current{wf_zstack}"), wf_dict)

        shutil.copy(
            os.path.join("workflows", f"current{wf_zstack}"),
            os.path.join("workflows", "workflow.txt"),
        )

        scope.check_workflow(
            command_queue,
            send_event,
            other_data_queue,
            COMMAND_CODES_CAMERA_CHECK_STACK,
        )
        print("sending workflow")
        scope.send_workflow(
            command_queue, send_event, system_idle,
        )
        image_data = scope.resolve_workflow(
            stage_location_queue,
            center_point,
            image_queue,
            visualize_event,
            terminate_event,
        )
        if terminate_event.is_set():
            print("Multiangle terminate triggered")
            break

    if terminate_event.is_set():
        print("Multiangle Collection terminating")
        terminate_event.clear()
        return
