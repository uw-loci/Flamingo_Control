#import tcpip_nuc
import functions.text_file_parsing
#, visualization_thread
import functions.calculations
from functions.microscope_connect import start_connection, create_threads, close_connection, get_microscope_settings, go_to_XYZR
import shutil

#from PIL import Image, ImageOps

#import multiprocessing as mp
import numpy as np

import time


def locate_sample( image_queue, command_queue, z_plane_queue, intensity_queue, view_snapshot, system_idle, processing_event, send_event, terminate_event, data0_queue, data1_queue, data2_queue, value_queue, stage_location_queue, laser_channel = "Laser 3 488 nm", laser_setting = '5.00 1', z_search_depth = 2.0, data_storage_location = '/media/deploy/MSN_LS'):
    '''
    Main command/control thread for LOCATING AND IF SAMPLE. Takes in some hard coded values which could potentially be handled through a GUI.
    Goes to the tip of the sample holder, then proceeds downward taking MIPs to find the sample. 
    Currently uses a provided IF channel to find a maxima, but it might be possible to use brightfield LEDs to find a minima.
    TO DO: handle different magnifications, search ranges

    WiFi warning: image data transfer will be very slow over wireless networks - use hardwired connections
    '''

    ## Validate that the following information is correct by presenting it in a GUI

     # Check the workflow file under Illumination Source
    #laser_channel = "Laser 1 640 nm" # Check the workflow file under Illumination Source

     # A string that contains'laser power(double) On/Off(1/0)' with On=1 and Off=0

    nuc_client, live_client, pixel_size, wf_snapshot, wf_zstack, LED_on, LED_off = start_connection()
    #coordinates for metal sample holder on Elsa
    # x_init = 14.17
    # y_init = 1.737
    # z_init = 13.7
    # r_init = 0
    x_init = 13.37
    y_init = 1.7
    z_init = 13.7
    r_init = 0
    ##########



    #commands

    c_scope_settings_request = 4105 #COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD
    c_scope_settings_returned = 4103
    #c_control_settings = 4107
    c_workflow = 12292 # workflow
    c_getStagePos = 24584
    c_setStagePos = 24580
    c_StageStopCheck = 24592
    c_StagePosGet = 54584
    c_snap = 12294
    c_update_live = 4119
    c_command_update= 36869
    c_idle_state = 40962
    ##############################

    ##
    #Queues and Events for keeping track of what state the software/hardware is in
    #and pass information between threads (Queues)
    ##



    # Start the threads to send commands, process data, and receive both command data and image data

    live_listen_thread_var, command_listen_thread_var,send_thread_var,processing_thread_var=create_threads(c_scope_settings_returned,c_idle_state,c_workflow, nuc_client, live_client, image_queue, command_queue, z_plane_queue, intensity_queue, view_snapshot, system_idle, processing_event, send_event, terminate_event, data0_queue, data1_queue, data2_queue, value_queue, stage_location_queue)
    


    voxel_dimensions, scope_settings = get_microscope_settings(command_queue, c_scope_settings_request, send_event)
    #FOV = image_pixel_size*number_of_pixels
    y_move = 0.4
    ############
    ymax = float(scope_settings['Stage limits']['Soft limit max y-axis'])
    print(f'ymax is {ymax}')
    ###############

    go_to_XYZR(data0_queue, data1_queue, data2_queue, value_queue, command_queue, send_event, c_setStagePos,x_init, y_init, z_init, r_init)
    ####################


    #Brightfield image to verify sample holder location
    print(f"coordinates x: {x_init}, y: {y_init}, z:{z_init}, r:{r_init}")
    snap_dict = text_file_parsing.workflow_to_dict("workflows/"+wf_snapshot)
    snap_dict['Experiment Settings']['Save image drive'] = data_storage_location #'/media/deploy/'+ USB_drive_name
    snap_dict['Experiment Settings']['Save image directory'] = 'Sample Search'
    snap_dict['Experiment Settings']['Save image data in tiff format'] = 'false'
    snap_dict['Experiment Settings']['Comments'] = 'Brightfield'
    snap_dict['Start Position']['X (mm)'] = x_init
    snap_dict['Start Position']['Y (mm)'] = y_init
    snap_dict['Start Position']['Z (mm)'] = z_init
    snap_dict['End Position']['Z (mm)'] = z_init+0.01
    snap_dict['End Position']['X (mm)'] = x_init
    snap_dict['End Position']['Y (mm)'] = y_init
    snap_dict['Start Position']['Angle (degrees)'] = r_init
    snap_dict['End Position']['Angle (degrees)'] = r_init
    snap_dict['Stack Settings']['Change in Z axis (mm)'] = 0.01
    text_file_parsing.dict_to_workflow("workflows/current"+wf_snapshot, snap_dict)

    #WORKFLOW.TXT FILE IS ALWAYS USED FOR send_event
    shutil.copy("workflows/current"+wf_snapshot, 'workflows/workflow.txt')
    #take a snapshot
    print('Acquire a brightfield snapshot')
    command_queue.put(c_workflow)
    send_event.set()

    while not system_idle.is_set():
        time.sleep(0.1)
    #Clear out collected data
    processing_event.set()
    top25_percentile_mean = intensity_queue.get()
    #Move half of a frame down from the current position
    ######how to determine half of a frame generically?######

    #images = []
    coords = []
    # initialize the active coordinates
    x = x_init
    y = y_init
    z = z_init
    r = r_init
    zstart = float(z) -float(z_search_depth)/2
    zend = float(z) +float(z_search_depth)/2
    print(f"coordinates x: {x}, y: {y}, z:{z}, r:{r}")
    # Settings for the Z-stacks
    wf_dict = text_file_parsing.workflow_to_dict("workflows/"+wf_zstack)
    # wf_dict['Illumination Source'][laser_channel] = "0.00 0"
    # wf_dict['Illumination Source']['LED_RGB_Board'] = LED_on
    wf_dict['Experiment Settings']['Save image drive'] = data_storage_location
    wf_dict['Experiment Settings']['Save image data in tiff format'] = 'false'
    wf_dict['Experiment Settings']['Save image directory'] = 'Sample Search'
    wf_dict['Experiment Settings']['Comments'] = 'Delete'
    wf_dict['Stack Settings']['Change in Z axis (mm)'] = z_search_depth
    wf_dict['Stack Settings']['Number of planes'] = round(1000*float(z_search_depth)/10)
    wf_dict['Experiment Settings'] ['Plane spacing (um)'] = str(10)
    wf_dict['Illumination Source'][laser_channel] = str(laser_setting)+' 1' # 1 indicates that the laser should be used/on.
    wf_dict['Illumination Source']['LED_RGB_Board'] = LED_off
    framerate = 40.0032 #/s
    wf_dict['Stack Settings']['Stage velocity (mm/s)']  = str(10*framerate/1000) #10um spacing and conversion to mm/s

    #Loop through a set of Y positions (increasing is "lower" on the sample)
    # check for a terminated thread or that the search range has gone "too far" which is instrument dependent
    #Get a max intensity projection at each Y and look for a peak that could represent the sample
    #Sending the MIP reduces the amount of data sent across the network, minimizing total time
    #Store the position of the peak and then go back to that stack and try to find the focus
    i=0
    while not terminate_event.is_set() and y_init+y_move*i <ymax: #change to ymax
        print("starting loop round " + str(i+1))
        print("*")      
        #adjust the Zstack position based on the last snapshot Z position
        # All adjustments are performed on the wf_dict, 
        wf_dict['Start Position']['X (mm)'] = str(x)
        wf_dict['Start Position']['Y (mm)'] = str(y) #increment y (down the sample tube)
        wf_dict['Start Position']['Z (mm)'] = zstart
        wf_dict['Start Position']['Angle (degrees)'] = r
        wf_dict['End Position']['X (mm)'] = str(x)
        wf_dict['End Position']['Y (mm)'] = str(y)
        wf_dict['End Position']['Z (mm)'] = zend
        wf_dict['End Position']['Angle (degrees)'] = r
        
        # Write a new workflow based on new Y positions
        text_file_parsing.dict_to_workflow("workflows/current"+wf_zstack, wf_dict)
        
        #Additional step for records that nothing went wrong if swapping between snapshots and Zstacks
        shutil.copy("workflows/current"+wf_zstack, 'workflows/workflow.txt')
        print(f"coordinates x: {x}, y: {y}, z:{z}, r:{r}")
        
        #print('before acquire Z'+ str(i+1))
        command_queue.put(c_workflow)
        send_event.set()

        while not system_idle.is_set():
            time.sleep(0.1)
        stage_location_queue.put([x,y,z,r])
        # visualize_event.set()
        # time.sleep(0.1)
        #print('after acquire Z'+ str(i+1))
        print('processing set')
        processing_event.set()
        top25_percentile_mean = intensity_queue.get()
        #print(f'intensity sum is {intensity}')
        #Store data about IF signal at current in focus location
        coords.append([x,y,z,r,top25_percentile_mean])


        #Loop may finish early if drastic maxima in intensity sum is detected
        
        top25_percentile_means = [coord[4] for coord in coords]
        print(f'Intensity means: {top25_percentile_means}')
        if (maxima :=calculations.check_maxima(top25_percentile_means)):
            break
        #move the stage up
        y = y + y_move
        i=i+1

    x = coords[maxima][0]
    y = coords[maxima][1]
    z = coords[maxima][2]
    r = coords[maxima][3]
    print(f"Sample located at x: {x}, y: {y}, r:{r}")
    print('Finding focus.')

    go_to_XYZR(data0_queue, data1_queue, data2_queue, value_queue, command_queue, send_event, c_setStagePos,x,y,z,r)

    #Take final Z stack at sample to find focus - maybe skip at the end but interesting to test
    wf_dict['Start Position']['X (mm)'] = str(x)
    wf_dict['Start Position']['Y (mm)'] = str(y) #increment y (down the sample tube)
    wf_dict['Start Position']['Z (mm)'] = zstart
    wf_dict['Start Position']['Angle (degrees)'] = r
    wf_dict['End Position']['X (mm)'] = str(x)
    wf_dict['End Position']['Y (mm)'] = str(y)
    wf_dict['End Position']['Z (mm)'] = zend
    wf_dict['End Position']['Angle (degrees)'] = r
    #collect many sub-MIPs, which requires less data transfer
    wf_dict['Experiment Settings']['Display max projection'] = "true"
    wf_dict['Experiment Settings']['Work flow live view enabled'] = "false"

    #Wireless is too slow and the nuc buffer is only 10 images, which can lead to overflow and deletion before the local computer pulls the data
    #for Z stacks larger than 10, make sure to split them into 10 image components and wait for each to complete.
    planes = float(wf_dict['Stack Settings']['Number of planes'])

    #number of image planes the nuc can/will hold in its buffer before overwriting
    #check with Joe Li before increasing this above 10. Decreasing it below 10 is fine.
    buffer_max = 10

    #loop through the total number of planes, 10 planes at a time
    loops = int(planes/buffer_max + 0.5)
    step_size_mm = float(wf_dict['Experiment Settings'] ['Plane spacing (um)'])/1000
    wf_dict['Stack Settings']['Change in Z axis (mm)'] = step_size_mm*buffer_max
    wf_dict['Stack Settings']['Number of planes'] = buffer_max
    combined_stack = []
    for i in range(loops):
        print(f'Subset of planes acquisition {i} in {loops}')
        wf_dict['Start Position']['Z (mm)'] = str(float(z) -float(z_search_depth)/2 + i*buffer_max*step_size_mm)
        wf_dict['End Position']['Z (mm)'] = str(float(z) - float(z_search_depth)/2 + (i+1)*buffer_max*step_size_mm)

        text_file_parsing.dict_to_workflow("workflows/current"+wf_zstack, wf_dict)
        shutil.copy("workflows/current"+wf_zstack, 'workflows/workflow.txt')
        print(f"start: {wf_dict['Start Position']['Z (mm)']}, end: {wf_dict['End Position']['Z (mm)']}")
        command_queue.put(c_workflow)
        send_event.set()

        while not system_idle.is_set():
            time.sleep(0.1)        
        new_image_stack=image_queue.get()
        #Add an axis so that the 2D images can stack, and the 3D array can be sent to processing
        new_image_stack = np.expand_dims(new_image_stack, axis=0)
        if combined_stack:
            combined_stack.append(new_image_stack)
        else:
            combined_stack = [new_image_stack]
        #print(f'combined stack length {len(combined_stack)}')
    #merge all of the stacks into a single data structure
    combined_stack = np.concatenate(combined_stack, axis=0)
    #place the data in the queue for the processing thread to access
    print(f'combined Zstack shape {combined_stack.shape}')
    image_queue.put(combined_stack)

    while not system_idle.is_set():
        time.sleep(0.1)
    print('after acquire Z final')

    print('processing set')
    processing_event.set()
    ########################
    #find the most in focus MIP, which for an IF channel is expected to be the brightest
    #should be in z_plane_queue
    queue_z = z_plane_queue.get()
    print("z focus plane "+str(queue_z))
    ########################
    #calculate the Z position for that slice

    step = float(wf_dict['Experiment Settings']['Plane spacing (um)'])*0.001 #convert to mm
    #Find the Z location for the snapshot, starting from the lowest value in the Z search range
    # 0.5 is subtracted from "queue_z" to find the middle of one of the MIPs, which is made up of "buffer_max" individual "step"s
    zSnap = min(zstart, zend)+step*buffer_max*(float(queue_z)-0.5)
    ######################
    print(f"Object located at {zSnap}")
    ## ??SET HOME TO LOCATION OF SAMPLE??

    #Move to correct Z position, command 24580
    data0_queue.put(3) #zaxis 
    data1_queue.put(0)
    data2_queue.put(0)
    value_queue.put(zSnap)
    command_queue.put(c_setStagePos) #movement
    send_event.set()
    while not command_queue.empty():
        time.sleep(.1)

    #Take a snapshot there using the user defined laser and power

    ##############
    snap_dict = text_file_parsing.workflow_to_dict("workflows/current"+wf_snapshot)
    snap_dict['Experiment Settings']['Save image directory'] = 'Sample'
    snap_dict['Experiment Settings']['Comments'] = 'Sample located'
    snap_dict['Illumination Source'][laser_channel] = str(laser_setting)
    snap_dict['Illumination Source']['LED_RGB_Board'] = LED_off
    snap_dict['Start Position']['X (mm)'] = str(x)
    snap_dict['Start Position']['Y (mm)'] = str(y) #increment y (down the sample tube)
    snap_dict['Start Position']['Angle (degrees)'] = r
    snap_dict['End Position']['X (mm)'] = str(x)
    snap_dict['End Position']['Y (mm)'] = str(y)
    snap_dict['End Position']['Angle (degrees)'] = r
    snap_dict['Start Position']['Z (mm)'] = str(zSnap)
    snap_dict['End Position']['Z (mm)'] = str(zSnap+0.005)   

    text_file_parsing.dict_to_workflow("workflows/current"+wf_snapshot, snap_dict)
    shutil.copy("workflows/current"+wf_snapshot, 'workflows/workflow.txt')
    print(f"Starting Snapshot")
    command_queue.put(c_workflow)
    send_event.set()   
    while not system_idle.is_set():
        time.sleep(0.1)
    print('Shutting down connection')
    ###
    #Clean up "delete" PNG files or dont make them
    ###
    
    close_connection(nuc_client, live_client,live_listen_thread_var, command_listen_thread_var,send_thread_var,processing_thread_var)

    exit()