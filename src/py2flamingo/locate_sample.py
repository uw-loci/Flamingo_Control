# TO DO? heavy use of workflow text files could probably be reduced to logging, with dictionaries being sent instead.
# TO DO? Modify to handle other types of workflows than Zstacks
# HARDCODED plane spacing and framerate
#TODO use sample_count. Not sure where to store, maybe a text file
#TODO X axis search?

import copy
import shutil
import time
import numpy as np
import functions.calculations as calc
from functions.microscope_connect import *
from functions.text_file_parsing import *
from global_objects import clear_all_events_queues

plane_spacing = 10
framerate = 40.0032  # /s


def initial_setup(command_queue, other_data_queue, send_event):
    clear_all_events_queues()
    # Look in the functions/command_list.txt file for other command codes, or add more
    commands = text_to_dict(
        os.path.join("src", "py2flamingo", "functions", "command_list.txt")
    )

    # Testing fidelity
    # print(commands)
    # dict_to_text('functions/command_test.txt', commands)

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
    stage_location_queue,
    system_idle: Event,
    xyzr_init: Sequence[float],
    visualize_event,
    image_queue
):
    workflow_dict=workflow_to_dict(os.path.join("workflows", "workflow.txt"))
    if not check_coordinate_limits(workflow_dict):
        return
    command_queue.put(COMMAND_CODES_CAMERA_WORK_FLOW_START)
    send_event.set()

    while not system_idle.is_set():
        time.sleep(0.1)

    stage_location_queue.put(xyzr_init)
    visualize_event.set()
    image_data = image_queue.get()
    return image_data


def search_sample_z_stacks(
    i: int,
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
    maxima = None

    while not terminate_event.is_set() and (float(xyzr_init[1]) + y_move * i) < ymax:
        print("Starting Y axis search " + str(i + 1))
        print("*")

        # adjust the Zstack position based on the last snapshot Z position
        wf_dict = dict_positions(wf_dict, xyzr, zEnd=zend)

        # Write a new workflow based on new Y positions
        dict_to_workflow(os.path.join("workflows", "current" + wf_zstack), wf_dict)
        dict_to_text(os.path.join("workflows", "current_test_" + wf_zstack), wf_dict)

        # Additional step for records that nothing went wrong if swapping between snapshots and Zstacks
        shutil.copy(
            os.path.join("workflows", "current" + wf_zstack),
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

        image_data = send_workflow(
            command_queue,
            send_event,
            stage_location_queue,
            system_idle,
            xyzr_centered,
            visualize_event,
            image_queue
        )

        _, y_intensity_map = calc.calculate_rolling_y_intensity(image_data, 21)

        # Store data about IF signal at current in focus location
        coords.append([copy.deepcopy(xyzr), y_intensity_map])

        # Loop may finish early if drastic maxima in intensity sum is detected
        processing_output_full = [y_intensity for coord in coords for _, y_intensity in coord[1]]

        if maxima := calc.check_maxima(processing_output_full, window_size = 500):
            break

        # move the stage up
        xyzr[1] = float(xyzr[1]) + y_move
        i = i + 1

    return maxima, coords, xyzr, i

#TODO fine Z focus to find edges of sample
def process_z_stack(
    i: int,
    loops: int,
    xyzr: list,
    z_init: float,
    z_search_depth_mm: float,
    z_step_depth: float,
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
    maxima,
    terminate_event
):
    """
    Function that can be used within a loop to collect the MIP of a Z stack. Outside the function, the brightness of the MIPs are tracked
    in order to determine the brightest MIP across the set of Z stacks.
    """
    print(f"Subset of planes acquisition {i} of {loops-1}")
    # Check for cancellation from GUI
    if terminate_event.is_set():
        print("Find Sample terminating")
        terminate_event.clear()
        return None, None
    # calculate the next step of the Z stack and apply that to the workflow file
    xyzr[2] = float(z_init) - float(z_search_depth_mm) / 2 + i * z_step_depth
    zEnd = float(z_init) - float(z_search_depth_mm) / 2 + (i + 1) * z_step_depth

    print(f'zstart and end {xyzr[2]}, {zEnd}')
    dict_positions(wf_dict, xyzr, zEnd = zEnd, save_with_data=False, get_zstack=False)

    dict_to_workflow(os.path.join("workflows", "current" + wf_zstack), wf_dict)

    shutil.copy(
        os.path.join("workflows", "current" + wf_zstack),
        os.path.join("workflows", "workflow.txt"),
    )
    check_workflow(command_queue, send_event, other_data_queue, COMMAND_CODES_CAMERA_CHECK_STACK)

    xyzr_centered = copy.deepcopy(xyzr)
    xyzr_centered[2] = (float(xyzr_centered[2]) + zEnd) / 2

    image_data = send_workflow(
        command_queue,
        send_event,
        stage_location_queue,
        system_idle,
        xyzr_centered,
        visualize_event,
        image_queue
    )

    mean_largest_quarter, _ = calc.calculate_rolling_y_intensity(image_data, 21)
    coordsZ.append([copy.deepcopy(xyzr_centered), mean_largest_quarter])
    top25_percentile_means = [coord[1] for coord in coordsZ]

    print(f"Intensity means: {top25_percentile_means}")
    if maxima := calc.check_maxima(top25_percentile_means):
        print(f'max position {maxima}')

    return top25_percentile_means, coordsZ, maxima, image_data


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
    laser_setting
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
    image_data = send_workflow(
        command_queue,
        send_event,
        stage_location_queue,
        system_idle,
        xyzr_init,
        visualize_event,
        image_queue
    )

    return image_data


def locate_sample(
    connection_data: list,
    sample_name,
    sample_count: int,
    xyzr_init: list,
    visualize_event,
    other_data_queue,
    image_queue,
    command_queue,
    system_idle: Event,
    send_event,
    terminate_event,
    command_data_queue,
    stage_location_queue,
    laser_channel="Laser 3 488 nm",
    laser_setting="5.00 1",
    z_search_depth_mm=2.0,
    data_storage_location=os.path.join("/media", "deploy", "MSN_LS"),
):
    """
    The main command/control function for locating and identifying a sample.

    This function executes a sample finding workflow. It moves the microscope to the tip of the 
    sample holder and proceeds downward, taking Maximum Intensity Projections (MIPs) to locate the sample.
    The sample identification is based on finding an intensity maxima in the provided imaging channel.

    Parameters:
    connection_data (list): Contains instances for network connections and settings.
    sample_count (int): Number of samples to be located.
    xyzr_init (list): Initial coordinates (x,y,z) and rotation (r).
    system_idle (Event): An event flag indicating if the system is idle.
    stage_location_queue: A queue to hold stage location data.
    laser_channel (str, optional): Laser channel to be used. Default is "Laser 3 488 nm".
    laser_setting (str, optional): Laser power setting to be used. Default is "5.00 1". The first number is % power, the second is off or on (0 or 1)
    z_search_depth_mm (float, optional): Depth for Z search. Default is 2.0.
    data_storage_location (str, optional): Path to save data. Default is os.path.join("/media", "deploy", "MSN_LS").

    Note: 
    * Function operation may be slow over wireless networks, it is recommended to use hardwired connections.
    """
    # in case of second run, clear out any remaining data or flags.
    command_labels, ymax, y_move, image_pixel_size_mm, frame_size = initial_setup(
        command_queue, other_data_queue, send_event
    )
    nuc_client, live_client, wf_zstack, LED_on, LED_off = connection_data
    (
        COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD,
        COMMAND_CODES_CAMERA_WORK_FLOW_START,
        COMMAND_CODES_STAGE_POSITION_SET,
        COMMAND_CODES_CAMERA_PIXEL_FIELD_Of_VIEW_GET,
        COMMAND_CODES_CAMERA_IMAGE_SIZE_GET,
        COMMAND_CODES_CAMERA_CHECK_STACK,
    ) = command_labels
    ##############################

    ##
    # Queues and Events for keeping track of what state the software/hardware is in
    # and pass information between threads (Queues)
    ##

    go_to_XYZR(command_data_queue, command_queue, send_event, xyzr_init)
    ####################

    # acquire_brightfield_image(
    #     command_queue,
    #     send_event,
    #     stage_location_queue,
    #     system_idle,
    #     xyzr_init,
    #     visualize_event,
    #     image_queue,
    #     wf_zstack,
    #     framerate,
    #     plane_spacing,
    #     laser_channel,
    #     laser_setting
    # )
    

    # initialize the active coordinates: starting Z position is treated as the middle of the search depth
    z_init = xyzr_init[2]
    zstart = float(z_init) - float(z_search_depth_mm) / 2
    zend = float(z_init) + float(z_search_depth_mm) / 2
    xyzr = [xyzr_init[0], xyzr_init[1], zstart, xyzr_init[3]]

    # Settings for the Z-stacks, assuming an IF search
    wf_dict = workflow_to_dict(os.path.join("workflows", wf_zstack))
    wf_dict = laser_or_LED(wf_dict, laser_channel, laser_setting, LED_off, LED_on, True)
    wf_dict["Experiment Settings"]["Save image drive"] = data_storage_location
    wf_dict["Experiment Settings"]["Save image directory"] = "Sample Search"
    wf_dict = dict_comment(wf_dict, "Delete")
    ####################HARDCODE WARNING#
    wf_dict = calculate_zplanes(wf_dict, z_search_depth_mm, framerate, plane_spacing)

    ##############################################################################
    # Loop through a set of Y positions (increasing is 'lower' on the sample)
    # check for a terminated thread or that the search range has gone 'too far' which is instrument dependent
    # Get a max intensity projection at each Y and look for a peak that could represent the sample
    # Sending the MIP reduces the amount of data sent across the network, minimizing total time
    # Store the position of the peak and then go back to that stack and try to find the focus
    i = 0
    maxima = False
    coords = []
    # xyzr_init[1] is the initial y position
    maxima, coords, xyzr, i = search_sample_z_stacks(
        i=0,
        ymax=ymax,
        xyzr=xyzr,
        xyzr_init=xyzr_init,
        y_move=y_move,
        wf_dict=wf_dict,
        zend=zend,
        wf_zstack=wf_zstack,
        command_queue=command_queue,
        send_event=send_event,
        other_data_queue=other_data_queue,
        COMMAND_CODES_CAMERA_CHECK_STACK=COMMAND_CODES_CAMERA_CHECK_STACK,
        stage_location_queue=stage_location_queue,
        system_idle=system_idle,
        visualize_event=visualize_event,
        image_queue=image_queue,
        image_pixel_size_mm=image_pixel_size_mm,
        terminate_event=terminate_event
    )
    processing_output_full = [y_intensity for coord in coords for _, y_intensity in coord[1]]
    bottom_bound_y, top_bound_y = calc.find_peak_bounds(processing_output_full, method="mode_std", background_percentage=10)
    print(f"yBounds detected at {bottom_bound_y} and {top_bound_y}")

    # Check for cancellation from GUI or if no sample is found
    if maxima is False:
        print('No sample found, returning to GUI')
        return
    if terminate_event.is_set():
        print("Find Sample terminating")
        terminate_event.clear()
        return

    print(f'Maxima found {maxima} pixels below the start point')
    #Multiply out the number of pixels times the pixel size, convert to mm, and add to the initial start position in mm
    #Subtract an additional half frame to center the object.
    print(f'y init {xyzr_init[1]} + distance searched {maxima*image_pixel_size_mm}')
    xyzr[1] = float(xyzr_init[1]) + (maxima-float(frame_size)/2) *image_pixel_size_mm
    print(f"Sample located at x: {xyzr[0]}, y: {xyzr[1]}, r:{xyzr[3]}")
    print("Finding focus in Z.")

    # Not really necessary as the workflow will handle this.
    # mostly a demonstration of using the go_to_XYZR function
    go_to_XYZR(command_data_queue, command_queue, send_event, xyzr)

    # Wireless is too slow and the nuc buffer is only 10 images, which can lead to overflow and deletion before the local computer pulls the data
    # for Z stacks larger than 10, make sure to split them into 10 image components and wait for each to complete.
    total_number_of_planes = z_search_depth_mm/float(wf_dict['Experiment Settings']['Plane spacing (um)'])

    # number of image planes the nuc can/will hold in its buffer before overwriting
    # check with Joe Li before increasing this above 10. Decreasing it below 10 is fine.
    buffer_max = 10
    ###################################################################################
    # loop through the total number of planes, 10 planes at a time
    loops = int(total_number_of_planes / buffer_max + 0.5)
    step_size_mm = float(wf_dict["Experiment Settings"]["Plane spacing (um)"]) / 1000
    z_step_depth = step_size_mm * buffer_max
    print(f" z search depth {z_search_depth_mm}")
    wf_dict = calculate_zplanes(wf_dict, z_step_depth, framerate, plane_spacing)

    coordsZ = []
    maxima = False
    for i in range(loops):
        top25_percentile_means, coordsZ, maxima, _ = process_z_stack(
            i=i,
            loops=loops,
            xyzr=xyzr,
            z_init=z_init,
            z_search_depth_mm=z_search_depth_mm,
            z_step_depth=z_step_depth,
            wf_dict=wf_dict,
            wf_zstack=wf_zstack,
            command_queue=command_queue,
            send_event=send_event,
            other_data_queue=other_data_queue,
            COMMAND_CODES_CAMERA_CHECK_STACK=COMMAND_CODES_CAMERA_CHECK_STACK,
            stage_location_queue=stage_location_queue,
            system_idle=system_idle,
            visualize_event=visualize_event,
            image_queue=image_queue,
            coordsZ=coordsZ,
            maxima=maxima,
            terminate_event=terminate_event
        )

        if maxima:
            break

    bottom_bound_z, top_bound_z = calc.find_peak_bounds(top25_percentile_means, method="mode_std", background_percentage=10)
    print(f"zBounds detected at {bottom_bound_z} and {top_bound_z}")
    
    print(f'maxima is {maxima}')
    z_positions = [point[0][2] for point in coordsZ]
    if not maxima:
        maxima = np.argmax(top25_percentile_means)


    print("z focus plane " + str(z_positions[maxima]))

    # calculate the Z position for that slice

    # step = float(wf_dict['Experiment Settings']['Plane spacing (um)'])*0.001 #convert to mm
    # Find the Z location for the snapshot, starting from the lowest value in the Z search range
    # 0.5 is subtracted from 'queue_z' to find the middle of one of the MIPs, which is made up of 'buffer_max' individual 'step's
    zSnap = z_positions[maxima]
    ######################
    print(f"Object located at {zSnap}")
    print(f'Midpoint of bounds located at {(bottom_bound_z+top_bound_z)/2}')

    # XYR should all be correct already
    # Move to correct Z position, command 24580
    command_data_queue.put([3, 0, 0, zSnap])

    command_queue.put(COMMAND_CODES_STAGE_POSITION_SET)  # movement
    send_event.set()
    while not command_queue.empty():
        time.sleep(0.1)

    # Take a snapshot there using the user defined laser and power

    ##############
    snap_dict = wf_dict
    #only the z position should have changed over the last search
    xyzr[2] = zSnap
    print(f"final xyzr snap {xyzr}")
    snap_dict["Experiment Settings"]["Save image directory"] = "Sample"
    snap_dict = dict_comment(snap_dict, "Sample located")

    snap_dict = dict_to_snap(snap_dict, xyzr, framerate, plane_spacing)
    snap_dict = laser_or_LED(snap_dict, laser_channel, laser_setting, laser_on=True)
    dict_to_workflow(os.path.join("workflows", "current" + wf_zstack), snap_dict)
    shutil.copy(
        os.path.join("workflows", "current" + wf_zstack),
        os.path.join("workflows", "workflow.txt"),
    )
    image_data = send_workflow(
        command_queue,
        send_event,
        stage_location_queue,
        system_idle,
        xyzr,
        visualize_event,
        image_queue
    )
    #Check initial IF peak
    ROLLING_AVERAGE_WIDTH = 101
    _, intensity_list_map = calc.calculate_rolling_x_intensity(image_data, ROLLING_AVERAGE_WIDTH)
    x_intensities = [intensity for _, intensity in intensity_list_map]
    bottom_bound_x, top_bound_x = calc.find_peak_bounds(x_intensities, method="mode_std", background_percentage=10)
    print(f"xBounds detected at {bottom_bound_x} and {top_bound_x}")

    max_x = np.argmax(x_intensities)
    print(f'current x {xyzr[0]}')
    print(f'max_x {max_x}')
    # print(f'shift in mm {max_x*image_pixel_size_mm}')
    xyzr[0]=xyzr[0]-max_x*image_pixel_size_mm
    print(f'adjusted xyzr {xyzr}')
    #TODO maybe locate the center of the bounding box?

    #TODO figure out these values using calculations
    xyzr_top = [5,5,5,0]
    xyzr_bottom = [8,8,8,0]
    location_path = os.path.join('sample_txt',sample_name, "bounds_"+sample_name+".txt")

    # store the bounding box coordinates in a dict
    bounding_dict = {
        "bounding box 1": {
            "x (mm)": top_bound_x,
            "y (mm)": top_bound_y,
            "z (mm)": top_bound_z,
            "r (°)": xyzr_top[3],
        },
        "bounding box 2": {
            "x (mm)": bottom_bound_x,
            "y (mm)": bottom_bound_y,
            "z (mm)": bottom_bound_z,
            "r (°)": xyzr_bottom[3],
        }
    }
    # bounding_dict = {
    #     "bounding box 1": {
    #         "x (mm)": xyzr_top[0],
    #         "y (mm)": xyzr_top[1],
    #         "z (mm)": xyzr_top[2],
    #         "r (°)": xyzr_top[3],
    #     },
    #     "bounding box 2": {
    #         "x (mm)": xyzr_bottom[0],
    #         "y (mm)": xyzr_bottom[1],
    #         "z (mm)": xyzr_bottom[2],
    #         "r (°)": xyzr_bottom[3],
    #     }
    # }
    dict_to_text(location_path, bounding_dict)

    print('All done with finding the sample(s)!')

