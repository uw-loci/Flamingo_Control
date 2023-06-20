#TODO check the find maxima code for a variable to control the background count (x axis pixels vs frames, 2000 vs 3)
import shutil
import copy
from functions.image_display import save_png
from locate_sample import process_z_stack, initial_setup, send_workflow
from functions.calculations import fit_ellipse_with_ransac, calculate_rolling_x_intensity
from functions.microscope_connect import *
from functions.text_file_parsing import *
from global_objects import clear_all_events_queues

import numpy as np

# number of image planes the nuc can/will hold in its buffer before overwriting
# check with Joe Li before increasing this above 10. Decreasing it below 10 is fine.
buffer_max = 10
plane_spacing = 10
framerate = 40.0032  # /s

def find_sample_at_angle(wf_dict: tuple,z_search_depth_mm: float,z_init_mm: float, image_pixel_size_mm: float,frame_size: int, xyzr, wf_zstack: str, command_queue, send_event, other_data_queue, COMMAND_CODES_CAMERA_CHECK_STACK, stage_location_queue, system_idle, visualize_event, image_queue, terminate_event):
    #Hardcoded search parameter at the moment, seems to work fine for zebrafish and 2048x2048 cameras.
    ROLLING_AVERAGE_WIDTH = 101
        # Wireless is too slow and the nuc buffer is only 10 images, which can lead to overflow and deletion before the local computer pulls the data
    # for Z stacks larger than 10, make sure to split them into 10 image components and wait for each to complete.
    total_number_of_planes = z_search_depth_mm*1000/float(wf_dict['Experiment Settings']['Plane spacing (um)'])
    print(f'total number of planes {total_number_of_planes}')
    # print(f'pixel size {image_pixel_size_mm}')
    ##################################### FIND IN Z ##############################################
    # loop through the total number of planes, 10 planes at a time
    loops = int(total_number_of_planes / buffer_max + 0.5)
    step_size_mm = float(wf_dict["Experiment Settings"]["Plane spacing (um)"]) / 1000
    z_step_depth = step_size_mm * buffer_max
    # print(f" z search depth {z_search_depth_mm}")
    wf_dict = calculate_zplanes(wf_dict, z_step_depth, framerate, plane_spacing)

    coordsZ = []
    maxima = False
    for i in range(loops):       
        top25_percentile_means, coordsZ, maxima, image_data = process_z_stack(
            i=i,
            loops=loops,
            xyzr=xyzr,
            z_init=z_init_mm,
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
    print(f"Intensity means: {top25_percentile_means}")
    #print(f'maxima is {maxima}')
    z_positions = [point[0][2] for point in coordsZ]
    if not maxima:
        maxima = np.argmax(top25_percentile_means)
    # Find the Z location for the snapshot, starting from the lowest value in the Z search range
    # 0.5 is subtracted from 'queue_z' to find the middle of one of the MIPs, which is made up of 'buffer_max' individual 'step's
    zSnap = z_positions[maxima]
    xyzr[2] = zSnap
    print(f'coordinates after z focus {xyzr}')
##############################Center X################
    #Check initial IF peak
    _, intensity_list_map = calculate_rolling_x_intensity(image_data, ROLLING_AVERAGE_WIDTH)
    x_intensities = [intensity for _, intensity in intensity_list_map]
    max_x = np.argmax(x_intensities)
    # print(f'current x {xyzr[0]}')
    # print(f'max_x {max_x}')
    # print(f'shift in mm {max_x*image_pixel_size_mm}')
    xyzr[0]=xyzr[0]-max_x*image_pixel_size_mm
    #print(f'adjusted xyzr {xyzr}')
    snap_dict = wf_dict
    #only the z position should have changed over the last search
    snap_dict["Experiment Settings"]["Save image directory"] = "Locate_by_angle"
    snap_dict = dict_comment(snap_dict, f'Current_angle {xyzr[3]}')
    #Loop until max intensity is in the center
    max_x = 0
    # Calculate the center of the frame
    center = frame_size / 2

    # Calculate the 10% range around the center
    lower_limit = center - 0.1 * frame_size
    upper_limit = center + 0.1 * frame_size
    # Loop until 'max_x' is within 10% of the center

    #Stopgap measure for testing
    i=0
    #
    while not (lower_limit <= max_x <= upper_limit) or i>5:
        i=i+1    
        snap_dict = dict_to_snap(snap_dict, xyzr, framerate, plane_spacing)
        dict_to_workflow(os.path.join("workflows", "current" + wf_zstack), snap_dict)
        shutil.copy(
            os.path.join("workflows", "current" + wf_zstack),
            os.path.join("workflows", "workflow.txt"),
        )
        print('Sending snapshot')
        image_data = send_workflow(
            command_queue,
            send_event,
            stage_location_queue,
            system_idle,
            xyzr,
            visualize_event,
            image_queue
        )
        save_png(image_data, f'{xyzr[3]} X pos {xyzr[0]}')
        # print('after snapshot')
        _, intensity_list_map = calculate_rolling_x_intensity(image_data, ROLLING_AVERAGE_WIDTH)
        x_intensities = [intensity for _, intensity in intensity_list_map]
        max_x = np.argmax(x_intensities)
        print(f'current x {xyzr[0]}')
        print(f'max_x {max_x}')

        xyzr[0] = xyzr[0] - (frame_size / 2 - max_x) * image_pixel_size_mm
        print(f'adjusted x {xyzr[0]}')
    #Update the workflow with the new coordinates
    snap_dict = dict_to_snap(snap_dict, xyzr, framerate, plane_spacing)
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
    save_png(image_data, f'{xyzr[3]} post-X adjusted')
    ## check if the center is center, iterate
    return xyzr

def save_ellipse_params(sample_name, params, angle_step_size_deg, xyzr):
    """
    Save the parameters of the ellipse fit to a text file.

    The parameters are saved in a dictionary format with the keys "h", "k", "a", and "b". 
    The output file is named with the pattern "{sample_name}_{angle_step_size_deg}_deg_ellipse_params.txt" 
    and saved to the "sample_txt/{sample_name}" directory.

    Parameters
    ----------
    sample_name : str
        The name of the sample which the ellipse parameters are associated with.
    params : tuple
        A tuple containing the parameters (h, k, a, b) of the fitted ellipse. 
        h, k: the coordinates of the center of the ellipse.
        a, b: the semi-major and semi-minor axes of the ellipse, respectively.
    angle_step_size_deg : int or float
        The increment in degrees that was used to collect data for the ellipse fit.

    Returns
    -------
    None
    """

    # Create a dictionary with the sample name as the key and the ellipse parameters as the values.
    ellipse_parameters = {
        "Ellipse parameters": {
            "h": params[0],  # The x-coordinate of the center of the ellipse
            "k": params[1],  # The y-coordinate of the center of the ellipse
            "a": params[2],  # The length of the semi-major axis
            "b": params[3],  # The length of the semi-minor axis
        },
        "Additional information":{
            "Angle step size (deg)": angle_step_size_deg,
            "Y position (mm)": xyzr[1]
        }
    }

    # Define the path for the output text file.
    # The file will be saved in the "sample_txt/{sample_name}" directory,
    # and the filename will follow the pattern "{sample_name}_{angle_step_size_deg}_deg_ellipse_params.txt".
    output_path = str(
        os.path.join(
            "sample_txt",
            f"{sample_name}",
            f"{sample_name}_ellipse_params.txt",
        )
    )

    # Save the parameters to a text file using the `dict_to_text` function.
    dict_to_text(output_path, ellipse_parameters)


def trace_ellipse(
    connection_data: list,
    xyzr_init: list,
    visualize_event,
    other_data_queue,
    image_queue,
    command_queue,
    system_idle: Event,
    processing_event,
    send_event,
    terminate_event,
    stage_location_queue,
    angle_step_size_deg = 20,
    sample_name = 'Default',
    laser_channel="Laser 3 488 nm",
    laser_setting="5.00 1",
    z_search_depth_mm=2.0,
    data_storage_location=os.path.join("/media", "deploy", "MSN_LS"),):
    """
    Take in a position and imaging settings, return the equation of an ellipse that fits the three sets of coordinates found.
    1. Rotate the sample 120 degrees.
    2. Search Z for the sample.
    3. Store the coordinates.
    4. Repeat 1-3 with another 120 degree rotation.
    5. Store the results to a text file (easier to double check if something goes wrong)
    ??? Name the text file based on... date+time? Another user field with name?
    """
    clear_all_events_queues()
    _, _, wf_zstack, LED_on, LED_off = connection_data
    command_labels, _, _, image_pixel_size_mm, frame_size =initial_setup(command_queue, other_data_queue, send_event)
    (
        COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD,
        COMMAND_CODES_CAMERA_WORK_FLOW_START,
        COMMAND_CODES_STAGE_POSITION_SET,
        COMMAND_CODES_CAMERA_PIXEL_FIELD_Of_VIEW_GET,
        COMMAND_CODES_CAMERA_IMAGE_SIZE_GET,
        COMMAND_CODES_CAMERA_CHECK_STACK,
    ) = command_labels
    wf_dict = workflow_to_dict(os.path.join("workflows", wf_zstack))
    wf_dict = laser_or_LED(wf_dict, laser_channel, laser_setting, LED_off, LED_on, True)
    wf_dict["Experiment Settings"]["Save image drive"] = data_storage_location
    wf_dict["Experiment Settings"]["Save image directory"] = "Sample Search"
    wf_dict = dict_comment(wf_dict, "Delete")
    
    wf_dict = calculate_zplanes(wf_dict, z_search_depth_mm, framerate, plane_spacing)
    print('Setup for ellipse scan complete')
    _, scope_settings = get_microscope_settings(command_queue, other_data_queue, send_event)
    z_init_mm=np.mean([float(scope_settings['Stage limits']['Soft limit max z-axis']), float(scope_settings['Stage limits']['Soft limit min z-axis'])])

    #First point, generally at 0 degrees
    xyzr_init=[float(x) for x in xyzr_init]
    xyzr = copy.deepcopy(xyzr_init)
    print(f'Initial coordinates {xyzr}')
    ellipse_points = []
    ellipse_points.append(copy.deepcopy(xyzr_init))
    for i in range(1, int(360/angle_step_size_deg)):
        #For large angle step sizes keeping xyzr can be problematic so restoring the initial values works better
        #xyzr = copy.deepcopy(xyzr_init)        
        xyzr[3]=xyzr_init[3]+i*angle_step_size_deg
        #steal function to locate sample in Z from locate sample. Also move the function out of locate_sample
        print(f'Beginning search at {xyzr}')
        new_point = find_sample_at_angle(wf_dict,z_search_depth_mm, z_init_mm, image_pixel_size_mm,frame_size, xyzr, wf_zstack, command_queue, send_event, other_data_queue, COMMAND_CODES_CAMERA_CHECK_STACK, stage_location_queue, system_idle, visualize_event, image_queue, terminate_event)
        ellipse_points.append(copy.deepcopy(new_point))
        print(f'ellipse points {ellipse_points}')

    params= fit_ellipse_with_ransac(ellipse_points)
    save_ellipse_params(sample_name, params, angle_step_size_deg)
    print(ellipse_points)


