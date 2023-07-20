import copy
import shutil
import time
import numpy as np
import functions.calculations as calc
from functions.microscope_connect import *
from functions.text_file_parsing import *
from global_objects import clear_all_events_queues
import functions.image_display
import queue

def initial_setup(command_queue, other_data_queue, send_event):
    """
    Essentially, this function generates some values that will be useful for downstream processes within a given function.
    Sends some information to the microscope nuc, and returns
    command_labels: list of command codes
    ymax: boundaries for Y
    y_move: distance for one frame to move in the Y direction and not overlap
    image_pixel_size_mm: size of a pixel in mm, in the image (not the camera pixel size)
    frame_size: the number of pixels in a frame, assumed to be square TODO: don't assume square and replace with [x,y,z?]
    """
    clear_all_events_queues()
    # Look in the functions/command_list.txt file for other command codes, or add more
    commands = text_to_dict(
        os.path.join("src", "py2flamingo", "functions", "command_list.txt")
    )


    COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD = int(
        commands["CommandCodes.h"]["COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD"]
    )
    # COMMAND_CODES_COMMON_SCOPE_SETTINGS  = int(commands['CommandCodes.h']['COMMAND_CODES_COMMON_SCOPE_SETTINGS'])
    COMMAND_CODES_CAMERA_WORK_FLOW_START = int(
        commands["CommandCodes.h"]["COMMAND_CODES_CAMERA_WORK_FLOW_START"]
    )
    COMMAND_CODES_STAGE_POSITION_SET = int(
        commands["CommandCodes.h"]["COMMAND_CODES_STAGE_POSITION_SET"]
    )
    # COMMAND_CODES_SYSTEM_STATE_IDLE  = int(commands['CommandCodes.h']['COMMAND_CODES_SYSTEM_STATE_IDLE'])
    COMMAND_CODES_CAMERA_PIXEL_FIELD_Of_VIEW_GET = int(
        commands["CommandCodes.h"]["COMMAND_CODES_CAMERA_PIXEL_FIELD_Of_VIEW_GET"]
    )
    COMMAND_CODES_CAMERA_IMAGE_SIZE_GET = int(
        commands["CommandCodes.h"]["COMMAND_CODES_CAMERA_IMAGE_SIZE_GET"]
    )
    COMMAND_CODES_CAMERA_CHECK_STACK = int(
        commands["CommandCodes.h"]["COMMAND_CODES_CAMERA_CHECK_STACK"]
    ) 

    command_labels = [
        COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD,
        COMMAND_CODES_CAMERA_WORK_FLOW_START,
        COMMAND_CODES_STAGE_POSITION_SET,
        COMMAND_CODES_CAMERA_PIXEL_FIELD_Of_VIEW_GET,
        COMMAND_CODES_CAMERA_IMAGE_SIZE_GET,
        COMMAND_CODES_CAMERA_CHECK_STACK,
    ]

    image_pixel_size_mm, scope_settings = get_microscope_settings(
        command_queue, other_data_queue, send_event
    )
    command_queue.put(COMMAND_CODES_CAMERA_IMAGE_SIZE_GET)
    send_event.set()
    time.sleep(0.1)

    frame_size = other_data_queue.get()
    FOV = image_pixel_size_mm * frame_size

    # pixel_size*frame_size #pixel size in mm*number of pixels per frame
    y_move = FOV #* 1.3 # increase step size for large samples and coarse search
    print(f"y_move search step size is currently {y_move}mm")
    ############
    ymax = float(scope_settings["Stage limits"]["Soft limit max y-axis"])
    print(f"ymax is {ymax}")
    ###############
    return command_labels, ymax, y_move, image_pixel_size_mm, frame_size


def check_workflow(command_queue, send_event, other_data_queue, COMMAND_CODES_CAMERA_CHECK_STACK):
    other_data_queue.empty()
    command_queue.put(COMMAND_CODES_CAMERA_CHECK_STACK)
    send_event.set()
    while send_event.isSet():
        time.sleep(0.05)
    text_bytes = other_data_queue.get()
    if "hard limit" in str(text_bytes):
        text_data = text_bytes.decode('utf-8')
        print(text_data)


def send_workflow(
    command_queue,
    send_event,
    system_idle: Event,
):
    workflow_dict=workflow_to_dict(os.path.join("workflows", "workflow.txt"))
    if not check_coordinate_limits(workflow_dict):
        return
    command_queue.put(COMMAND_CODES_CAMERA_WORK_FLOW_START)
    send_event.set()

    #TODO this is a hacky way to avoid the problem that I am not getting all of the system idle event messages as of 7/11/2023. This has not happened before.
    start_time = time.time()
    while not system_idle.is_set():
        #check to see if we missed the idle command
        if time.time() - start_time > 5: 
            command_queue.put(COMMAND_CODES_SYSTEM_STATE_GET)
            send_event.set()
            start_time = time.time()
        time.sleep(0.1)

def resolve_workflow(
        stage_location_queue,
        xyzr_init,
        image_queue,
        visualize_event,
        terminate_event,
    ):
    """
    To be run immediately after
    """
    stage_location_queue.put(xyzr_init)
    visualize_event.set()
    # Check for image data or terminate_event
    while True:
        try:
            return image_queue.get(timeout=1) # Wait for 1 second
        except queue.Empty:
            if terminate_event.is_set():
                # Terminate event is set, break the loop
                break

    # Return None or some other appropriate response if terminated
    return None

def replace_none(values, replacement):
    """
    Function to replace None values in a list of two elements.
    If the first element is None, replace it with 0.
    If the second element is None, replace it with the provided integer.

    Parameters:
    values (list): The input list with two elements.
    replacement (int): The integer to replace the second element if it is None.

    Returns:
    list: The modified list with None values replaced.
    """
    for bounds in values:
        if bounds[0] is None:
            bounds[0] = 0
            print("Bounds edge hit - 0")
        if bounds[1] is None:
            bounds[1] = replacement
            print("Bounds edge hit - max")

    return values



def y_axis_sample_boundary_search(
    sample_count: int,
    ymax: float,
    xyzr: list,
    xyzr_init: list,
    y_move: float,
    wf_dict: dict,
    zend: float,
    wf_zstack: str,
    command_queue,
    send_event,
    other_data_queue,
    COMMAND_CODES_CAMERA_CHECK_STACK,
    stage_location_queue,
    system_idle,
    visualize_event,
    image_queue,
    image_pixel_size_mm,
    terminate_event
):
    """
    Function that takes in information about a starting location and enough variables to run some workflows to scan down the Y axis. 
    It returns coordinates and intensities that can be used to predict the locations of samples.
    Maxima are detected using a rolling average of horizontal lines in the image.
    """
    coords = []
    i = 0
    while not terminate_event.is_set() and (float(xyzr_init[1]) + y_move * i) < ymax:
        print(f"Starting Y axis search {str(i + 1)}")
        print("*")

        # adjust the Zstack position based on the last snapshot Z position
        wf_dict = dict_positions(wf_dict, xyzr, zEnd=zend)

        # Write a new workflow based on new Y positions
        dict_to_workflow(os.path.join("workflows", f"current{wf_zstack}"), wf_dict)
        dict_to_text(os.path.join("workflows", f"current_test_{wf_zstack}"), wf_dict)

        # Additional step for records that nothing went wrong if swapping between snapshots and Zstacks
        shutil.copy(
            os.path.join("workflows", f"current{wf_zstack}"),
            os.path.join("workflows", "workflow.txt"),
        )
        #Minor adjustment - since we are taking the MIP of a Z stack, use the center of that Z stack
        #rather than the starting or ending position
        xyzr_centered = xyzr.copy()
        xyzr_centered[2] = (float(xyzr_centered[2]) + zend) / 2
        print(
            f"coordinates x: {xyzr[0]}, y: {xyzr[1]}, z:{xyzr_centered[2]}, r:{xyzr[3]}"
        )

        check_workflow(command_queue, send_event, other_data_queue, COMMAND_CODES_CAMERA_CHECK_STACK)

        send_workflow(
            command_queue,
            send_event,
            system_idle,

        )
        image_data = resolve_workflow(
            stage_location_queue,
            xyzr_init,
            image_queue,
            visualize_event,
            terminate_event,
        )
        functions.image_display.save_png(image_data, f'yscan_{xyzr_centered[1]}')
        _, y_intensity_map = calc.calculate_rolling_y_intensity(image_data, 21)

        # Store data about IF signal at current in focus location
        coords.append([copy.deepcopy(xyzr), y_intensity_map])

        # Loop may finish early if drastic maxima in intensity sum is detected
        processing_output_full = [y_intensity for coord in coords for _, y_intensity in coord[1]]

        # if maxima := calc.check_maxima(processing_output_full, window_size = 100):
        #     break
        bounds = calc.find_peak_bounds(processing_output_full, num_peaks=sample_count)
        if bounds is not None and all(b is not None for sublist in bounds for b in sublist):
            print(f'bounds {bounds}')
            break

        # move the stage up
        xyzr[1] = float(xyzr[1]) + y_move
        i += 1

    return bounds, coords, xyzr, i

#TODO fine Z focus to find edges of sample?
def z_axis_sample_boundary_search(
    i: int,
    loops: int,
    xyzr: list,
    z_init,
    z_search_depth_mm,
    z_step_depth_mm,
    wf_dict: dict,
    wf_zstack: str,
    command_queue,
    send_event,
    other_data_queue,
    COMMAND_CODES_CAMERA_CHECK_STACK,
    stage_location_queue,
    system_idle,
    visualize_event,
    image_queue,
    coordsZ,
    terminate_event
):
    """
    Function that can be used within a loop to collect the MIP of a Z stack. Outside the function, the brightness of the MIPs are tracked
    in order to determine the brightest MIP across the set of Z stacks.
    z_step_depth_mm - the depth in Z for each sub-Z stack.
    z_search_depth_mm - total search range through Z, usually limited by the Z axis bounds in the software for a particular sytem
    z_init = Starting central position within the Z-search range
    """
    print(f"Subset of planes acquisition {i} of {loops-1}")

    # calculate the next step of the Z stack and apply that to the workflow file
    xyzr[2] = float(z_init) - float(z_search_depth_mm) / 2 + i * z_step_depth_mm
    zEnd = float(z_init) - float(z_search_depth_mm) / 2 + (i + 1) * z_step_depth_mm

    print(f'zstart and end {xyzr[2]}, {zEnd}')
    dict_positions(wf_dict, xyzr, zEnd = zEnd, save_with_data=False, get_zstack=False)

    dict_to_workflow(os.path.join("workflows", f"current{wf_zstack}"), wf_dict)

    shutil.copy(
        os.path.join("workflows", f"current{wf_zstack}"),
        os.path.join("workflows", "workflow.txt"),
    )
    check_workflow(command_queue, send_event, other_data_queue, COMMAND_CODES_CAMERA_CHECK_STACK)

    xyzr_centered = copy.deepcopy(xyzr)
    xyzr_centered[2] = (float(xyzr_centered[2]) + zEnd) / 2

    send_workflow(
        command_queue,
        send_event,
        system_idle,

    )
    image_data = resolve_workflow(
        stage_location_queue,
        xyzr_centered,
        image_queue,
        visualize_event,
        terminate_event,
    )
    #This doesn't really take the rolling Y intensity, as that part isn't kept. It's just used for the mean largest quarter value
    mean_largest_quarter, _ = calc.calculate_rolling_y_intensity(image_data, 3)
    coordsZ.append([copy.deepcopy(xyzr_centered), mean_largest_quarter])
    top25_percentile_means = [coord[1] for coord in coordsZ]

    #print(f"Intensity means: {top25_percentile_means}")
    # if maxima := calc.check_maxima(top25_percentile_means):
    #     #Once a maxima is assigned, the loop should break.
    #     print(f'max position {maxima}')

    #Don't start searching for peaks too early.
    if len(top25_percentile_means) > 4:
        if bounds := calc.find_peak_bounds(top25_percentile_means, threshold_pct=30):
            print(f'bounds {bounds}')
    else:
        bounds = [[None, None]]
    return top25_percentile_means, coordsZ, bounds, image_data


def acquire_brightfield_image(
    command_queue,
    send_event,
    stage_location_queue,
    system_idle,
    xyzr_init,
    visualize_event,
    image_queue,
    wf_zstack,
    framerate,
    plane_spacing,
    laser_channel,
    laser_setting,
    terminate_event
):
    """
    Acquires a brightfield image to verify sample holder location (assuming the sample holder is visible at the start
    coordinates).

    Parameters:
    command_queue: A queue to hold command data.
    send_event: An event flag used for send synchronization.
    stage_location_queue: A queue to hold stage location data.
    system_idle (Event): An event flag indicating if the system is idle.
    xyzr_init (list): Initial coordinates (x,y,z) and rotation (r).
    visualize_event: An event flag used for visualization synchronization.
    image_queue: A queue to hold image data.
    wf_zstack: The name of the workflow file for Z stack operation.
    framerate: The framerate to be used for snapshot operation.
    plane_spacing: The plane spacing to be used for snapshot operation.
    laser_channel (str): Laser channel to be used.
    laser_setting (str): Laser setting (%power and 1 or 0 to indicate on or off) to be used.
    """

    # Convert workflow to dictionary and adjust settings for snapshot
    snap_dict = workflow_to_dict(os.path.join("workflows", wf_zstack))
    snap_dict = dict_to_snap(snap_dict, xyzr_init, framerate, plane_spacing)
    snap_dict = laser_or_LED(snap_dict, laser_channel, laser_setting, laser_on=False)

    # Save the workflow dictionary to a file
    dict_to_workflow(os.path.join("workflows", "currentSnapshot.txt"), snap_dict)

    # Copy the workflow file to the always used workflow file
    shutil.copy(
        os.path.join("workflows", "currentSnapshot.txt"),
        os.path.join("workflows", "workflow.txt"),
    )

    # Acquire a brightfield snapshot and return the image data
    print("Acquire a brightfield snapshot")
    send_workflow(
        command_queue,
        send_event,
        system_idle,

    )
    return resolve_workflow(
        stage_location_queue,
        xyzr_init,
        image_queue,
        visualize_event,
        terminate_event,
    )
