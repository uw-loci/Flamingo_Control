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
from take_snapshot import take_snapshot
from functions.image_display import save_png
import functions.microscope_interactions as scope
plane_spacing = 10
framerate = 40.0032  # /s
BUFFER_MAX = 10


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
    command_labels, ymax, y_move, image_pixel_size_mm, frame_size = scope.initial_setup(
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
    xyzr_sample_top_mm = [None,None,None,None]
    xyzr_sample_bottom_mm = [None,None,None,None]
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

    bounds = [None, None]
    coords = []
    # xyzr_init[1] is the initial y position
    bounds, coords, xyzr, i = scope.y_axis_sample_boundary_search(
        sample_count,
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
    #if no bounds were found on either end of the search, use the ends of the search instead.
    bounds = scope.replace_none(bounds, ((i+1)*frame_size))
    #TODO handle multiple samples in a loop
    top_bound_y_px, bottom_bound_y_px = bounds[0]
    print(f'Bottom bounds y {bottom_bound_y_px} top bounds y {top_bound_y_px}')
    print(f'top addition {top_bound_y_px * image_pixel_size_mm} bottom addition {bottom_bound_y_px * image_pixel_size_mm}')
    xyzr_sample_bottom_mm[1] = float(xyzr_init[1]) + bottom_bound_y_px * image_pixel_size_mm
    xyzr_sample_top_mm[1] = float(xyzr_init[1]) + top_bound_y_px * image_pixel_size_mm
    #print(processing_output_full)
    print(f"yBounds detected at {xyzr_sample_bottom_mm[1]} and {xyzr_sample_top_mm[1]}")

    # Check for cancellation from GUI or if no sample is found
    if terminate_event.is_set():
        print("Find Sample terminating")
        terminate_event.clear()
        return

    #Multiply out the number of pixels times the pixel size, convert to mm, and add to the initial start position in mm
    #Subtract an additional half frame to center the object.
    print(f'y init {xyzr_init[1]} + distance searched {(i+1)*frame_size*image_pixel_size_mm}')
    sample_midpoint = (xyzr_sample_top_mm[1]+xyzr_sample_bottom_mm[1])/2
    #shift up half a frame so that the middle of the sample is in the middle of the imaging FOV
    frame_shift_midpoint = sample_midpoint-frame_size*image_pixel_size_mm/2
    xyzr[1] = frame_shift_midpoint

    print("Finding focus in Z.")

    # Not really necessary as the workflow will handle this.
    # mostly a demonstration of using the go_to_XYZR function
    go_to_XYZR(command_data_queue, command_queue, send_event, xyzr)

    # Wireless is too slow and the nuc buffer is only 10 images, which can lead to overflow and deletion before the local computer pulls the data
    # for Z stacks larger than 10, make sure to split them into 10 image components and wait for each to complete.
    total_number_of_planes = float(z_search_depth_mm)*1000/float(wf_dict['Experiment Settings']['Plane spacing (um)'])
    print(f" z depth {z_search_depth_mm}, plane spacing {float(wf_dict['Experiment Settings']['Plane spacing (um)'])}")
    # number of image planes the nuc can/will hold in its buffer before overwriting
    # check with Joe Li before increasing this above 10. Decreasing it below 10 is fine.

    ###################################################################################
    # loop through the total number of planes, 10 planes at a time
    loops = int(total_number_of_planes / BUFFER_MAX + 0.5)
    step_size_mm = float(wf_dict["Experiment Settings"]["Plane spacing (um)"]) / 1000 #um to mm
    z_step_depth_mm = step_size_mm * BUFFER_MAX
    print(f" z search depth {z_search_depth_mm}")
    wf_dict = calculate_zplanes(wf_dict, z_step_depth_mm, framerate, plane_spacing)

    coordsZ = []
    top25_percentile_means=None
    print(f'loops count {loops}')
    #Based on the center of the Y bounding box, search for the Z bounding box - simple search, may fail if the widest part of the sample isn't near the center
    for i in range(loops):
        top25_percentile_means, coordsZ, bounds, _ = scope.z_axis_sample_boundary_search(
            i=i,
            loops=loops,
            xyzr=xyzr,
            z_init=z_init,
            z_search_depth_mm=z_search_depth_mm,
            z_step_depth_mm=z_step_depth_mm,
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
            terminate_event=terminate_event
        )
        if terminate_event.is_set():
            break
        if bounds is not None and all(b is not None for sublist in bounds for b in sublist):
            print(f'bounds {bounds}')
            break

    if terminate_event.is_set():
        print("Find Sample terminating")
        terminate_event.clear()
        return
    print(f'xyzr is currently {xyzr}')
    #bounds = calc.find_peak_bounds(top25_percentile_means)
    bounds = scope.replace_none(bounds, loops)
    #TODO handle a loop for multiple objects
    bottom_bound_z_vx, top_bound_z_vx = bounds[0]

    zSearchStart = float(z_init) - float(z_search_depth_mm) / 2
    print(f'zstart {zSearchStart}, zsearchdepth {z_search_depth_mm}, zstepdepth {z_step_depth_mm}')
    xyzr_sample_bottom_mm[2] = zSearchStart + bottom_bound_z_vx * z_step_depth_mm
    xyzr_sample_top_mm[2] = zSearchStart + top_bound_z_vx * z_step_depth_mm
    midpoint_z_mm = (xyzr_sample_top_mm[2]+xyzr_sample_bottom_mm[2])/2
    print(f"zBounds detected at {xyzr_sample_top_mm[2]} and {xyzr_sample_bottom_mm[2]}")
    
    # z_positions = [point[0][2] for point in coordsZ]

    print("z focus plane " + str(midpoint_z_mm))

    # XYR should all be correct already
    # Move to correct Z position, command 24580
    command_data_queue.put([3, 0, 0, midpoint_z_mm])

    command_queue.put(COMMAND_CODES_STAGE_POSITION_SET)  # movement
    send_event.set()
    while not command_queue.empty():
        time.sleep(0.1)
    #update Z center for X search
    xyzr[2] = midpoint_z_mm
    # # Take a snapshot there using the user defined laser and power

    # ##############
    # snap_dict = wf_dict
    # #only the z position should have changed over the last search
    # xyzr[2] = midpoint_z_mm
    # print(f"final xyzr snap {xyzr}")
    # snap_dict["Experiment Settings"]["Save image directory"] = "Sample"
    # snap_dict = dict_comment(snap_dict, "Sample located")

    # snap_dict = dict_to_snap(snap_dict, xyzr, framerate, plane_spacing)
    # snap_dict = laser_or_LED(snap_dict, laser_channel, laser_setting, laser_on=True)
    # dict_to_workflow(os.path.join("workflows", "current" + wf_zstack), snap_dict)
    # shutil.copy(
    #     os.path.join("workflows", "current" + wf_zstack),
    #     os.path.join("workflows", "workflow.txt"),
    # )
    # image_data = scope.send_workflow(
    #     command_queue,
    #     send_event,
    #     stage_location_queue,
    #     system_idle,
    #     xyzr,
    #     visualize_event,
    #     image_queue
    # )
##############################Center X################
    ROLLING_AVERAGE_WIDTH = 101
    i=0
    #Make sure the bounding box is within the frame, if possible
    top_bound_x_px = 0
    bottom_bound_x_px = frame_size
    while (top_bound_x_px == 0 or bottom_bound_x_px == frame_size) or i<5:
        i=i+1
        #print(f'xloop {i}')
        x_before_move = xyzr[0]
        image_data = take_snapshot(
            connection_data,
            xyzr,
            visualize_event,
            other_data_queue,
            image_queue,
            command_queue,
            stage_location_queue,
            send_event,
            laser_channel,
            laser_setting,
        )
        save_png(image_data, f'{xyzr[3]} X pos {xyzr[0]}')
        # print('after snapshot')
        _, intensity_list_map = calc.calculate_rolling_x_intensity(image_data, ROLLING_AVERAGE_WIDTH)
        x_intensities = [intensity for _, intensity in intensity_list_map]

        bounds  = calc.find_peak_bounds(x_intensities)
        print(f'original x bounds {bounds}')
        if bounds is not None and all(b is not None for sublist in bounds for b in sublist):
            top_bound_x_px, bottom_bound_x_px = bounds[0]
        else:
            max_x = np.argmax(x_intensities)
            xyzr[0] = float(xyzr[0]) - (frame_size / 2 - max_x) * image_pixel_size_mm
        #TODO handle samples that are larger than 1 X axis frame
        #TODO possible check - find widest bounds along Y axis and store position, check that position in Y for X bounds beyond frame width.
        bounds = scope.replace_none(bounds, frame_size)
        #break out of loop if the movement is minor
        #This does not prevent oscillating between two points
        if abs(float(x_before_move) - float(xyzr[0])) <= 0.05:
            print('breaking out of x search loop')
            print(f' x values are {bounds}')
            break

    #At this point the bounding box should either be the entire image, or fully "in frame"
    #Calculate the bounding box positions in mm
    xyzr_sample_bottom_mm[0] = float(xyzr[0]) + bottom_bound_x_px * image_pixel_size_mm
    xyzr_sample_top_mm[0] = float(xyzr[0]) + top_bound_x_px * image_pixel_size_mm
    print(f"xBounds detected at {xyzr_sample_bottom_mm[0]} and {xyzr_sample_top_mm[0]}")
    x_sample_midpoint_mm = (xyzr_sample_top_mm[0]+xyzr_sample_bottom_mm[0])/2

    #Shift midpoint to the midpoint of the visible frame
    x_sample_midpoint_frameshift_mm =x_sample_midpoint_mm -frame_size*image_pixel_size_mm/2
    xyzr[0]= x_sample_midpoint_frameshift_mm
    
    location_path = os.path.join('sample_txt',sample_name, "bounds_"+sample_name+".txt")

    # store the bounding box coordinates in a dict
    bounding_dict = {
        "bounds 1": {
            "x (mm)": xyzr_sample_top_mm[0],
            "y (mm)": xyzr_sample_top_mm[1],
            "z (mm)": xyzr_sample_top_mm[2],
            "r (°)": xyzr[3],
        },
        "bounds 2": {
            "x (mm)": xyzr_sample_bottom_mm[0],
            "y (mm)": xyzr_sample_bottom_mm[1],
            "z (mm)": xyzr_sample_bottom_mm[2],
            "r (°)": xyzr[3],
        }
    }
    dict_to_text(location_path, bounding_dict)


    image_data = take_snapshot(
        connection_data,
        xyzr,
        visualize_event,
        other_data_queue,
        image_queue,
        command_queue,
        stage_location_queue,
        send_event,
        laser_channel,
        laser_setting,
    )
    save_png(image_data, f'{xyzr[3]} post-X adjusted')
    print('All done with finding the sample(s)!')

