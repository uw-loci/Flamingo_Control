laser_channel = "Laser 3 488 nm" # Check the workflow file under Illumination Source
laser_setting = '5.00 1' # A string that contains'laser power On/Off' with On=1 and Off=0
import tcpip_nuc
import workflow_gen
from threads import listener_thread, command_thread, processing_thread, workflow_thread
import misc
import socket
import shutil
from threading import Event, Thread
from PIL import Image, ImageOps
import socket
import multiprocessing as mp
import numpy as np
from queue import Queue
import time

#######CONNECTION START##########
NUC_IP = '10.129.37.17' #From Connection tab in GUI
PORT_NUC = 53717 #From Connection tab in GUI
PORT_LISTEN = PORT_NUC+1 #Live mode

print("socket connect")
client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
print(client)
client.connect((NUC_IP, PORT_NUC))


#print('listener thread socket created on ' + str(IP_REMOTE)+ ' : '+str(PORT_REMOTE))
#########CONNECTION END ###############

########DISCOVER INSTRUMENT ID#######
#######SET VARIABLES BASED ON INSTRUMENT#
#################################


#coordinates for metal sample holder on Elsa
x_init = 14.17
y_init = 1.737
z_init = 13.7
r_init = 0
##########
ymax = 10 # might need to read this from the system - system dependent
##########
search = 0.4 #distance in mm to search for focus


#commands
c_workflow = 12292 # workflow
c_getStagePos = 24584
c_setStagePos = 24580
c_StageStopCheck = 24592
c_snap = 12294
c_update_live = 4119
c_command_update= 36869
##############################
#print('first snap')
# returnedData = 0
# while returnedData ==0:
#     returnedData = tcpip_nuc.command_to_nuc(client, c_snap)
# print('returned '+ str(returnedData[2]))
#wf_file = 'workflows/workflow.txt'

#image_queue = Queue() #I think this only need to be defined in py
z_plane_queue = Queue()
intensity_queue = Queue()
command_queue = Queue()
terminate_event = Event()
acquire_event = Event()
processing_event = Event()
command_event = Event()

print("moving to tip of sample holder")
returnedData = tcpip_nuc.command_to_nuc(client, c_setStagePos, data0 = 0, value = x_init)
returnedData = tcpip_nuc.command_to_nuc(client, c_setStagePos, data0 = 1, value = y_init)
returnedData = tcpip_nuc.command_to_nuc(client, c_setStagePos, data0 = 2, value = z_init)
returnedData = tcpip_nuc.command_to_nuc(client, c_setStagePos, data0 = 3, value = r_init)
#returnedData = tcpip_nuc.command_to_nuc(client, c_command_update)

# Start the listener, generator, and processor threads
listener_thread = Thread(target=listener_thread, 
                         args=(NUC_IP, PORT_LISTEN, terminate_event, processing_event, intensity_queue))
workflow_thread = Thread(target=workflow_thread, 
                        args=(client, ymax, c_workflow, terminate_event, acquire_event))
processing_thread = Thread(target=processing_thread, 
                           args=(z_plane_queue, terminate_event, processing_event))
command_thread = Thread(target=command_thread, 
                        args=(client, command_event, command_queue))

listener_thread.start()
time.sleep(1)
workflow_thread.start()
processing_thread.start()
command_thread.start()


#get clientID - listen on 53717, 128 bytes, figure out data structure from systemcommands.h controllerIDGET
#print('second snap')

command_queue.put(c_StageStopCheck)
command_event.set()
while command_event.is_set():
    time.sleep(1)
print('stopped')

#UNCOMMENTING THIS DOES TRIGGER DATA FROM THE NUC
#returnedData = tcpip_nuc.command_to_nuc(client, c_snap)
#Workflow test
snap_dict = workflow_gen.workflow_to_dict("workflows/Snapshot.txt")
snap_dict['Illumination Source'][laser_channel] = laser_setting
snap_dict['Start Position']['X (mm)'] = x_init
snap_dict['Start Position']['Y (mm)'] = y_init
snap_dict['Start Position']['Z (mm)'] = z_init
snap_dict['End Position']['Z (mm)'] = z_init-0.01
snap_dict['End Position']['X (mm)'] = x_init
snap_dict['End Position']['Y (mm)'] = y_init
snap_dict['Start Position']['Angle (degrees)'] = r_init
snap_dict['End Position']['Angle (degrees)'] = r_init
snap_dict['Stack Settings']['Change in Z axis (mm)'] = 0.01
workflow_gen.dict_to_workflow("workflows/currentSnapshot.txt", snap_dict)

#WORKFLOW.TXT FILE IS ALWAYS USED FOR acquire_event
shutil.copy("workflows/currentSnapshot.txt", 'workflows/workflow.txt')
#take a snapshot
print('Acquire via a snapshot workflow')
acquire_event.set()
while acquire_event.is_set():
    time.sleep(0.1)
print('after acquire initial snap '+str(acquire_event.is_set()))


#start loop
#Move half of a frame down from the current position
######how to determine half of a frame generically?######
images = []
coords = []
intensity = intensity_queue.get()
print('intensity '+ str(intensity))
coords.append([x_init,y_init,z_init,r_init,])
intensity_queue = Queue()
print(coords)
# initialize the active coordinates
x = x_init
y = y_init
z = z_init
r = r_init

# send a command and listen for a response in a loop
wf_dict = workflow_gen.workflow_to_dict("workflows/ZStack.txt")
####
####maybe find max value for Y on instrument to set search range

#loop through changing the position of the acquired brightfield Z stack by updating the currentZstack.txt file
#Also update the position of currentSnapshot.txt to take IF images
# both txt files are copied to workflow.txt right before use since the functions in "threads.py" all use workflow.txt
i=0
while not terminate_event.is_set():
    print("starting loop round " + str(i+1))
    #adjust the Zstack position based on the last snapshot Z position
    # the last snapshot Z should be at the focus maxima
    snap_dict = workflow_gen.workflow_to_dict("workflows/currentSnapshot.txt")
    currentZ = snap_dict['Start Position']['Z (mm)']
    wf_dict['Start Position']['X (mm)'] = x
    wf_dict['Start Position']['Y (mm)'] = y+i*0.4
    wf_dict['Start Position']['Z (mm)'] = str(float(currentZ) +float(search/2))
    wf_dict['End Position']['Z (mm)'] = str(float(currentZ) - float(search)/2)
    wf_dict['Start Position']['Angle (degrees)'] = r
    wf_dict['End Position']['Y (mm)'] = str(float(y)+i*0.4)
    wf_dict['Stack Settings']['Change in Z axis (mm)'] = search
    # Write a new workflow based on new Y positions
    workflow_gen.dict_to_workflow("workflows/currentZStack.txt", wf_dict)
    shutil.copy("workflows/currentZStack.txt", 'workflows/workflow.txt')
    print('before acquire Z'+ str(i+1))
    acquire_event.set()
    while acquire_event.is_set():
        time.sleep(0.1)
    print('after acquire Z'+ str(i+1))
    ########################
    #find the most in focus slice
    #should be in z_plane_queue
    queue_z = z_plane_queue.get()
    print("z focus plane "+str(queue_z))
    ########################
    #calculate the Z position for that slice
    z1 = float(wf_dict['Start Position']['Z (mm)'])
    z2 = float(wf_dict['End Position']['Z (mm)'])
    step = float(wf_dict['Experiment Settings']['Plane spacing (um)'])*0.001 #convert to mm
    zSnap = min(z1, z2)+step*float(queue_z)
    ######################
    #Take a snapshot there using the user defined laser and power
    #####################
    #bad filler code
    #zSnap = (float(wf_dict['Start Position']['Z (mm)']) - float(wf_dict['End Position']['Z (mm)']))/2
    ##############
    snap_dict = workflow_gen.workflow_to_dict("currentSnapshot.txt")
    snap_dict['Start Position']['X (mm)'] = wf_dict['Start Position']['X (mm)']
    snap_dict['Start Position']['Y (mm)'] = wf_dict['Start Position']['Y (mm)']
    snap_dict['Start Position']['Z (mm)'] = str(zSnap)
    snap_dict['End Position']['Z (mm)'] = str(zSnap+0.005)
    snap_dict['Start Position']['Angle (degrees)'] = wf_dict['Start Position']['Angle (degrees)']
    workflow_gen.dict_to_workflow("currentSnapshot.txt", snap_dict)
    shutil.copy("workflows/currentZStack.txt", 'workflows/workflow.txt')
    acquire_event.set()
    #Store data about IF signal at current in focus location
    x= snap_dict['Start Position']['X (mm)'] 
    y= snap_dict['Start Position']['Y (mm)']
    z = zSnap 
    r = snap_dict['Start Position']['Angle (degrees)']
    coords.append([x,y,z,r,intensity_queue])
    intensity_queue = Queue()
    #Loop may finish early if drastic maxima in intensity sum is detected
    intensity_sums = [coord[4] for coord in coords]
    if (max :=misc.check_maxima(intensity_sums)):
        break
    i=i+1
    #Loop must finish when max Y reached
x = coords[max][0]
y = coords[max][1]
z = coords[max][2]
r = coords[max][3]
listener_thread.join()
command_thread.join()
processing_thread.join()
client.close()
returnedData = tcpip_nuc.command_to_nuc(client, c_setStagePos, data0 = 0, value = x)
returnedData = tcpip_nuc.command_to_nuc(client, c_setStagePos, data0 = 1, value = y)
returnedData = tcpip_nuc.command_to_nuc(client, c_setStagePos, data0 = 2, value = z)
returnedData = tcpip_nuc.command_to_nuc(client, c_setStagePos, data0 = 3, value = r)
tcpip_nuc.is_stage_stopped(client, c_StageStopCheck)
print("Object located... maybe")
## ??SET HOME TO LOCATION OF SAMPLE??