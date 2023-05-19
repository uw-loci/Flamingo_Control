'''
Main command/control thread for LOCATING AND IF SAMPLE. Takes in some hard coded values which could potentially be handled through a GUI.
Goes to the tip of the sample holder, then proceeds downward taking MIPs to find the sample. 
Currently uses a provided IF channel to find a maxima, but it might be possible to use brightfield LEDs to find a minima.
 TO DO: handle different magnifications, search ranges

 WiFi warning: image data transfer will be very slow over wireless networks - use hardwired connections
'''
laser_channel = "Laser 3 488 nm" # Check the workflow file under Illumination Source
#laser_channel = "Laser 1 640 nm" # Check the workflow file under Illumination Source

laser_setting = '5.00 1' # A string that contains'laser power(double) On/Off(1/0)' with On=1 and Off=0
LED_off = '00.00 0'
LED_on = '38.04 1'
z_search_depth = 2.0 #Depth of Z stack to search for sample in mm

#Workflow templates
#Current code requires EITHER Display max projection OR Work flow live view enabled
#but not both
wf_zstack = "ZStack.txt" #Fluorescent Z stack to find sample
wf_snapshot = "Snapshot.txt"
USB_drive_name = 'MSN_LS'
pixel_size = 6.5

#import tcpip_nuc
import text_file_parsing
from threads import command_listen_thread, processing_thread, send_thread, live_listen_thread
import calculations
import socket
import shutil
from threading import Event, Thread
#from PIL import Image, ImageOps
import socket
#import multiprocessing as mp
import numpy as np
from queue import Queue
import time
import os
########
if not os.path.exists("workflows"):
    os.makedirs("workflows")
if not os.path.exists("ouput_png"):
    os.makedirs("ouput_png")

#Function specific, validate that Snapshot.txt and ZStack.txt exist, or find way to make sure they don't need to exist

#######CONNECTION START##########
NUC_IP = '10.129.37.17' #From Connection tab in GUI
PORT_NUC = 53717 #From Connection tab in GUI
PORT_LISTEN = PORT_NUC+1 #Live mode data, on 53718

nuc_client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
nuc_client.connect((NUC_IP, PORT_NUC))
live_client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
live_client.connect((NUC_IP, PORT_LISTEN))

#print('listener thread socket created on ' + str(IP_REMOTE)+ ' : '+str(PORT_REMOTE))
#########CONNECTION END ###############


########DISCOVER INSTRUMENT ID#######
#######SET VARIABLES BASED ON INSTRUMENT#
#################################


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

c_scope_settings = 4105 #COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD
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
image_queue, command_queue, z_plane_queue, intensity_queue = Queue(), Queue(), Queue(), Queue()
view_snapshot, system_idle, processing_event, send_event, terminate_event = Event(), Event(), Event(), Event(), Event()
#queues to send data with commands
data0_queue, data1_queue, data2_queue, value_queue = Queue(),Queue(),Queue(),Queue()


# Start the threads to send commands, process data, and receive both command data and image data

send_thread = Thread(target=send_thread, 
                        args=(nuc_client, command_queue, send_event, system_idle, c_workflow, data0_queue, data1_queue, data2_queue, value_queue))
processing_thread = Thread(target=processing_thread, 
                           args=(z_plane_queue, terminate_event, processing_event, intensity_queue, image_queue))

command_listen_thread = Thread(target=command_listen_thread, 
                        args=(nuc_client, system_idle, terminate_event, c_idle_state, c_scope_settings))
live_listen_thread = Thread(target=live_listen_thread, 
                         args=(live_client, terminate_event, processing_event, image_queue))
live_listen_thread.start()
command_listen_thread.start()
send_thread.start()
processing_thread.start()

#####################
command_queue.put(c_scope_settings) #movement
send_event.set()
while not command_queue.empty():
    time.sleep(.3)

#microscope settings should now be in a text file called ScopeSettings.txt in the 'workflows' directory
#convert them into a dict to extract useful information
#########

scope_settings = text_file_parsing.settings_to_dict("workflows/ScopeSettings.txt")

################
#Occasionally there is an error on this next step, not entirely sure why. Inconsistent.
#######
objective_mag = float(scope_settings['Type']['Objective lens magnification'])
tube_lens_design_length = float(scope_settings['Type']['Tube lens design focal length (mm)'])
tube_lens_actual_length = float(scope_settings['Type']['Tube lens length (mm)'])
#calculate y_move from the above?
image_pixel_size = pixel_size*objective_mag*tube_lens_design_length/tube_lens_actual_length
#FOV = image_pixel_size*number_of_pixels
y_move = 0.4
############
ymax = float(scope_settings['Stage limits']['Soft limit max y-axis'])
print(f'ymax is {ymax}')
###############
print("moving to tip of sample holder")
#data0_queue.put(0) doesn't represent a motion axis
data0_queue.put(1) #xaxis 
data1_queue.put(0)
data2_queue.put(0)
value_queue.put(x_init)
command_queue.put(c_setStagePos) #movement
send_event.set()
while not command_queue.empty():
    time.sleep(.1)

data0_queue.put(3) #zaxis 
data1_queue.put(0)
data2_queue.put(0)
value_queue.put(z_init)
command_queue.put(c_setStagePos) #movement
send_event.set()
while not command_queue.empty():
    time.sleep(.1)

data0_queue.put(4) #rotation
data1_queue.put(0)
data2_queue.put(0)
value_queue.put(r_init)
command_queue.put(c_setStagePos) #movement
send_event.set()
while not command_queue.empty():
    time.sleep(.1)

data0_queue.put(2) #yaxis 
data1_queue.put(0)
data2_queue.put(0)
value_queue.put(y_init)
command_queue.put(c_setStagePos) #movement
send_event.set()
while not command_queue.empty():
    time.sleep(.1)


####################


#Brightfield image to verify sample holder location
print(f"coordinates x: {x_init}, y: {y_init}, z:{z_init}, r:{r_init}")
snap_dict = text_file_parsing.workflow_to_dict("workflows/"+wf_snapshot)
snap_dict['Experiment Settings']['Save image drive'] = '/media/deploy/'+ USB_drive_name
snap_dict['Experiment Settings']['Save image directory'] = 'Sample Search'
snap_dict['Experiment Settings']['Comments'] = 'Delete'
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

#Move half of a frame down from the current position
######how to determine half of a frame generically?######

images = []
coords = []
# initialize the active coordinates
x = x_init
y = y_init
z = z_init
r = r_init
zstart = float(z) -float(z_search_depth/2)
zend = float(z) +float(z_search_depth/2)
print(f"coordinates x: {x}, y: {y}, z:{z}, r:{r}")
# Settings for the Z-stacks
wf_dict = text_file_parsing.workflow_to_dict("workflows/"+wf_zstack)
# wf_dict['Illumination Source'][laser_channel] = "0.00 0"
# wf_dict['Illumination Source']['LED_RGB_Board'] = LED_on
wf_dict['Experiment Settings']['Save image drive'] = '/media/deploy/'+ USB_drive_name
wf_dict['Experiment Settings']['Save image directory'] = 'Sample Search'
wf_dict['Experiment Settings']['Comments'] = 'Delete'
wf_dict['Stack Settings']['Change in Z axis (mm)'] = z_search_depth
wf_dict['Stack Settings']['Number of planes'] = round(1000*z_search_depth/10)
wf_dict['Experiment Settings'] ['Plane spacing (um)'] = str(10)
wf_dict['Illumination Source'][laser_channel] = str(laser_setting)
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

#store this mess as a function
data0_queue.put(1) #xaxis 
data1_queue.put(0)
data2_queue.put(0)
value_queue.put(x)
command_queue.put(c_setStagePos) #movement
send_event.set()
while not command_queue.empty():
    time.sleep(.1)

data0_queue.put(3) #zaxis 
data1_queue.put(0)
data2_queue.put(0)
value_queue.put(z)
command_queue.put(c_setStagePos) #movement
send_event.set()
while not command_queue.empty():
    time.sleep(.1)

data0_queue.put(4) #rotation
data1_queue.put(0)
data2_queue.put(0)
value_queue.put(r)
command_queue.put(c_setStagePos) #movement
send_event.set()
while not command_queue.empty():
    time.sleep(.1)

data0_queue.put(2) #yaxis 
data1_queue.put(0)
data2_queue.put(0)
value_queue.put(y)
command_queue.put(c_setStagePos) #movement
send_event.set()
while not command_queue.empty():
    time.sleep(.1)

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
    #print(f'Subset of planes acquisition {i} in {loops}')
    wf_dict['Start Position']['Z (mm)'] = str(float(z) -float(z_search_depth/2) + i*buffer_max*step_size_mm)
    wf_dict['End Position']['Z (mm)'] = str(float(z) - float(z_search_depth)/2 + (i+1)*buffer_max*step_size_mm)

    text_file_parsing.dict_to_workflow("workflows/current"+wf_zstack, wf_dict)
    shutil.copy("workflows/current"+wf_zstack, 'workflows/workflow.txt')
    #print(f"start: {wf_dict['Start Position']['Z (mm)']}, end: {wf_dict['End Position']['Z (mm)']}")
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
snap_dict['Experiment Settings']['Comments'] = 'In focus'
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
send_thread.join()
live_listen_thread.join()
command_listen_thread.join()
processing_thread.join()
nuc_client.close()
live_client.close()

exit()