#TODO check the find maxima code for a variable to control the background count (x axis pixels vs frames, 2000 vs 3)
import shutil
import copy
from functions.image_display import save_png
import functions.microscope_interactions as scope
import functions.calculations as calc
import functions.microscope_connect as mc
import functions.text_file_parsing as txt
from take_snapshot import take_snapshot
import numpy as np
import os

# number of image planes the nuc can/will hold in its buffer before overwriting
# check with Joe Li before increasing this above 10. Decreasing it below 10 is fine.
BUFFER_MAX = 5
plane_spacing = 10
FRAMERATE = 40.0032  # /s

#Assumptions:
# Y depth should not change per angle, sample is rotating around the Y axis
# Bounding box may change size due to change in the angle of the sample??
#TODO need sample name to access bounding box
def find_sample_at_angle(connection_data, sample_name: str, wf_dict: tuple,z_search_depth_mm: float,z_init_mm: float, image_pixel_size_mm: float,frame_size: int, xyzr, wf_zstack: str, command_queue, command_data_queue, send_event, other_data_queue, COMMAND_CODES_CAMERA_CHECK_STACK, stage_location_queue, system_idle, visualize_event, image_queue, terminate_event, laser_channel, laser_setting):
    #Hardcoded search parameter at the moment, seems to work fine for zebrafish and 2048x2048 cameras.
    ROLLING_AVERAGE_WIDTH = 101
    # Wireless is too slow and the nuc buffer is only 10 images, which can lead to overflow and deletion before the local computer pulls the data
    # for Z stacks larger than 10, make sure to split them into 10 image components and wait for each to complete.
    total_number_of_planes = z_search_depth_mm*1000/float(wf_dict['Experiment Settings']['Plane spacing (um)'])
    print(f'total number of planes {total_number_of_planes}')
    #top and bottom coords are in [x,y,z,r] format
    #y and r will stay the same as passed, x and z will be recalculated
    xyzr_sample_top_mm = copy.deepcopy(xyzr)
    xyzr_sample_bottom_mm =copy.deepcopy(xyzr)

    ##################################### FIND IN Z ##############################################
    # loop through the total number of planes, 10 planes at a time
    loops = int(total_number_of_planes / BUFFER_MAX + 0.5)
    step_size_mm = float(wf_dict["Experiment Settings"]["Plane spacing (um)"]) / 1000
    z_step_depth_mm = step_size_mm * BUFFER_MAX
    # print(f" z search depth {z_search_depth_mm}")
    wf_dict = scope.calculate_zplanes(wf_dict, z_step_depth_mm, FRAMERATE, plane_spacing)

    coordsZ = []
    print(f'loops count {loops}')
    #Based on the center of the Y bounding box, search for the Z bounding box - simple search, may fail if the widest part of the sample isn't near the center
    for i in range(loops):
        _, coordsZ, bounds, _ = scope.z_axis_sample_boundary_search(
            i=i,
            loops=loops,
            xyzr=xyzr,
            z_init=z_init_mm,
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
            break

    if terminate_event.is_set():
        print("Find Sample terminating")
        terminate_event.clear()
        return
    print(f'xyzr is currently {xyzr}')

    bounds = scope.replace_none(bounds, loops)
    #TODO handle a loop for multiple objects
    bottom_bound_z_vx, top_bound_z_vx = bounds[0]

    zSearchStart = float(z_init_mm) - float(z_search_depth_mm) / 2
    print(f'zstart {zSearchStart}, zsearchdepth {z_search_depth_mm}, zstepdepth {z_step_depth_mm}')
    xyzr_sample_bottom_mm[2] = zSearchStart + bottom_bound_z_vx * z_step_depth_mm
    xyzr_sample_top_mm[2] = zSearchStart + top_bound_z_vx * z_step_depth_mm
    midpoint_z_mm = (xyzr_sample_top_mm[2]+xyzr_sample_bottom_mm[2])/2
    print(f"zBounds detected at {bottom_bound_z_vx} and {top_bound_z_vx}")
    print(f"zBounds detected at {xyzr_sample_top_mm[2]} and {xyzr_sample_bottom_mm[2]}")
    
    # z_positions = [point[0][2] for point in coordsZ]

    print("z focus plane " + str(midpoint_z_mm))

    # XYR should all be correct already
    xyzr[2] = midpoint_z_mm
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
##############################Center X################
    # i possibly temporary stopgap measure for testing - prevent infinite looping
    i=0
    #Make sure the bounding box is within the frame, if possible
    top_bound_x_px = 0
    bottom_bound_x_px = frame_size
    while (top_bound_x_px == 0 or bottom_bound_x_px == frame_size) or i<5:
        i=i+1
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
        #save_png(image_data, f'{xyzr[3]} X pos {xyzr[0]}')
        # print('after snapshot')
        _, intensity_list_map = calc.calculate_rolling_x_intensity(image_data, ROLLING_AVERAGE_WIDTH)
        x_intensities = [intensity for _, intensity in intensity_list_map]

        bounds  = calc.find_peak_bounds(x_intensities)
        if bounds is not None and all(b is not None for sublist in bounds for b in sublist):
            top_bound_x_px, bottom_bound_x_px = bounds[0]
            #xyzr[0] = (top_bound_x_px + bottom_bound_x_px/2) * image_pixel_size_mm - frame_size*image_pixel_size_mm/2
        else:
            max_x = np.argmax(x_intensities)
            xyzr[0] = float(xyzr[0]) - (frame_size / 2 - max_x) * image_pixel_size_mm
        #TODO handle samples that are larger than 1 X axis frame
        #TODO possible check - find widest bounds along Y axis and store position, check that position in Y for X bounds beyond frame width.
        bounds = scope.replace_none(bounds, frame_size)
        #break out of loop if the movement is minor
        #This does not prevent oscillating between two points
        if abs(x_before_move - xyzr[0]) <= 0.05:
            print('breaking out of x search loop')
            print(f' x values are {bounds}')
            break

    #At this point the bounding box should either be the entire image, or fully "in frame"
    #Calculate the bounding box positions in mm
    xyzr_sample_bottom_mm[0] = float(xyzr[0]) + bottom_bound_x_px * image_pixel_size_mm
    xyzr_sample_top_mm[0] = float(xyzr[0]) + top_bound_x_px * image_pixel_size_mm
    x_sample_midpoint_mm = (xyzr_sample_top_mm[0]+xyzr_sample_bottom_mm[0])/2
    #Shift midpoint to the midpoint of the visible frame
    x_sample_midpoint_frameshift_mm =x_sample_midpoint_mm -frame_size*image_pixel_size_mm/2
    xyzr[0] = x_sample_midpoint_frameshift_mm
    #Update the workflow with the new coordinates
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
    ## check if the center is center, iterate
    # bounding_dict = {
    #     "bounds 1": {
    #         "x (mm)": xyzr_sample_top_mm[0],
    #         "y (mm)": xyzr_sample_top_mm[1],
    #         "z (mm)": xyzr_sample_top_mm[2],
    #         "r (°)": xyzr[3],
    #     },
    #     "bounds 2": {
    #         "x (mm)": xyzr_sample_bottom_mm[0],
    #         "y (mm)": xyzr_sample_bottom_mm[1],
    #         "z (mm)": xyzr_sample_bottom_mm[2],
    #         "r (°)": xyzr[3],
    #     }
    # }
    # location_path = os.path.join('sample_txt',sample_name, "bounds_"+sample_name+"_"+ str(xyzr[3])+"deg.txt")
    # txt.dict_to_text(location_path, bounding_dict)
    return xyzr_sample_top_mm, xyzr_sample_bottom_mm



#TODO there seems to be some issue with the Y position selected, it is too large of a value
def trace_ellipse(
    connection_data: list,
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
    angle_step_size_deg = 10,
    sample_name = 'Default',
    laser_channel="Laser 3 488 nm",
    laser_setting="5.00 1",
    z_search_depth_mm=2.0,
    data_storage_location=os.path.join("/media", "deploy", "MSN_LS"),):
    """
    Trace an ellipse based on a single bounding box, generally acquired from the Locate Sample button. However, this could also be created manually and placed at sample_txt/{samplename}/bounds_{samplename}.txt
    Place new bounds_samplename_###deg.txt files as the sample is rotated, showing the new bounding box location at that angle.
    Currently creates a list of all inner, outer, and central bounding box coordinates for analysis. Only the inner and outer are saved into text files.
    """
    _, _, wf_zstack, LED_on, LED_off = connection_data
    command_labels, _, _, image_pixel_size_mm, frame_size = scope.initial_setup(command_queue, other_data_queue, send_event)
    (
        COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD,
        COMMAND_CODES_CAMERA_WORK_FLOW_START,
        COMMAND_CODES_STAGE_POSITION_SET,
        COMMAND_CODES_CAMERA_PIXEL_FIELD_Of_VIEW_GET,
        COMMAND_CODES_CAMERA_IMAGE_SIZE_GET,
        COMMAND_CODES_CAMERA_CHECK_STACK,
    ) = command_labels
    wf_dict = txt.workflow_to_dict(os.path.join("workflows", wf_zstack))
    wf_dict = scope.laser_or_LED(wf_dict, laser_channel, laser_setting, LED_off, LED_on, True)
    wf_dict["Experiment Settings"]["Save image drive"] = data_storage_location
    wf_dict["Experiment Settings"]["Save image directory"] = "Sample Search"
    wf_dict = txt.dict_comment(wf_dict, "Delete")
    
    wf_dict = txt.calculate_zplanes(wf_dict, z_search_depth_mm, FRAMERATE, plane_spacing)
    print('Setup for ellipse scan complete')
    _, scope_settings = mc.get_microscope_settings(command_queue, other_data_queue, send_event)
    #Find the middle of the possible Z search range
    z_init_mm=np.mean([float(scope_settings['Stage limits']['Soft limit max z-axis']), float(scope_settings['Stage limits']['Soft limit min z-axis'])])
########################################
    #First point, generally at 0 degrees
    # xyzr_init=[float(x) for x in xyzr_init]
    # xyzr = copy.deepcopy(xyzr_init)
    #################
    #Try taking the starting position from the current sample bounds
    location_path = os.path.join('sample_txt',sample_name, "sample_bounds_"+sample_name+".txt")

    # Check if the file exists
    if not os.path.isfile(location_path):
        print(f"Error: File {location_path} does not exist.")
        return
    
    # If the file exists, proceed to call text_to_dict
    starting_location_dict = txt.text_to_dict(location_path)
    top_bound, bottom_bound = txt.dict_to_bounds(starting_location_dict)
    #save these to reassign Y axis positions to bounding box. They will be lost when calculating the center position.
    y_top = top_bound[1]
    y_bottom = bottom_bound[1]
    frameshift = 0.5*frame_size*image_pixel_size_mm
    xyzr_init = calc.find_center(top_bound, bottom_bound, frameshift)

    xyzr = copy.deepcopy(xyzr_init)
    print(f'Initial coordinates {xyzr}')
    ellipse_points = []
    ellipse_points.append(copy.deepcopy(xyzr_init))
    top_ellipse_points = []
    bottom_ellipse_points = []
    top_ellipse_points.append(top_bound)
    bottom_ellipse_points.append(bottom_bound)
    for i in range(1, int(360/angle_step_size_deg)):
        #For large angle step sizes keeping xyzr can be problematic so restoring the initial values works better
        #xyzr = copy.deepcopy(xyzr_init)        
        xyzr[3]=xyzr_init[3]+i*angle_step_size_deg
        #steal function to locate sample in Z from locate sample. Also move the function out of locate_sample
        print(f'Beginning search at {xyzr}')
        top_bounds_mm, bottom_bounds_mm = find_sample_at_angle(connection_data, sample_name, wf_dict,z_search_depth_mm, z_init_mm, image_pixel_size_mm,frame_size, xyzr, wf_zstack, command_queue, command_data_queue, send_event, other_data_queue, COMMAND_CODES_CAMERA_CHECK_STACK, stage_location_queue, system_idle, visualize_event, image_queue, terminate_event, laser_channel, laser_setting)
        top_bounds_mm[1] = y_top
        bottom_bounds_mm[1] = y_bottom
        print(f'detected top bounds {top_bounds_mm}')
        ############BIG QUESTION, HOW TO DO?############
        #Two ellipses, one per part of bounding box, or is one fine, and predict orientation of bounding box?
        centerpoint_mm = calc.find_center(top_bounds_mm, bottom_bounds_mm, frameshift)
        #keep the sample centered as we rotate
        xyzr[0] = centerpoint_mm[0]
        #Maybe have top ellipse and bottom ellipse?
        top_ellipse_points.append(copy.deepcopy(top_bounds_mm))
        bottom_ellipse_points.append(copy.deepcopy(bottom_bounds_mm))
        ellipse_points.append(copy.deepcopy(centerpoint_mm))
        #print(f'ellipse points {ellipse_points}')
    top_points_dict = txt.points_to_dict(top_ellipse_points)
    bottom_points_dict = txt.points_to_dict(bottom_ellipse_points)
    txt.save_points_to_csv( sample_name, top_points_dict,"top")
    txt.save_points_to_csv(sample_name, bottom_points_dict,"bottom")
    location_path = os.path.join('sample_txt',sample_name, "top_bounds_"+sample_name+".txt")
    txt.dict_to_text(location_path, top_points_dict)
    location_path = os.path.join('sample_txt',sample_name, "bottom_bounds_"+sample_name+".txt")
    txt.dict_to_text(location_path, bottom_points_dict)
    print(f'top: {top_ellipse_points}')
    print(f'bottom: {bottom_ellipse_points}')
    print(f'center: {ellipse_points}')

    # try:
    #     params= calc.fit_ellipse_with_ransac(ellipse_points)
    #     txt.save_ellipse_params(sample_name, params, angle_step_size_deg, xyzr)
    # except ValueError:
    #     print("fit_ellipse_with_ransac failed for ellipse_points")
    # try:
    #     params_top = calc.fit_ellipse_with_ransac(top_ellipse_points)
    #     txt.save_ellipse_params(sample_name, params_top, angle_step_size_deg, xyzr)
    # except ValueError:
    #     print("fit_ellipse_with_ransac failed for top_ellipse_points")
    #     # Handle the failure case here

    # try:
    #     params_bottom = calc.fit_ellipse_with_ransac(bottom_ellipse_points)
    #     txt.save_ellipse_params(sample_name, params_bottom, angle_step_size_deg, xyzr)
    # except ValueError:
    #     print("fit_ellipse_with_ransac failed for bottom_ellipse_points")
    #     # Handle the failure case here
    
    

    #print(ellipse_points)


