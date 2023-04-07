laser_channel = "Laser 3 488 nm" # Check the workflow file under Illumination Source
laser_setting = '5.00 1' # A string that contains'laser power On/Off' with On=1 and Off=0
THREAD_GO = True
import tcpip_nuc
import workflow_gen
import misc
import socket
import struct
import threading
from PIL import Image, ImageOps
import socket
import multiprocessing as mp
import numpy as np
import listen

#######CONNECTION START##########
NUC_IP = '10.129.37.17' #From Connection tab in GUI
PORT_NUC = 53717 #From Connection tab in GUI
PORT_LISTEN = PORT_NUC+1 #Live mode
PORT_LISTEN_STACK = PORT_NUC+2 #Live mode
print("socket connect")
client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
print(client)
client.connect((NUC_IP, PORT_NUC))
#########CONNECTION END ###############

########DISCOVER INSTRUMENT ID#######
#######SET VARIABLES BASED ON INSTRUMENT#
#################################


#coordinates for metal sample holder on Elsa
x_init = 14.17
y_init = 1.737
z_init = 13.7
r_init = 0
#search = 0.4 #distance to search for focus


#commands
c_workflow = 12292 # workflow
c_snap = 4139 
c_getStagePos = 24584
c_setStagePos = 24580
c_StageStopCheck = 24592
c_snap = 12294

wf_file = 'defaultStack.txt'

def snap_thread(ip, port, index):
    grayscale_image = listen.listen_for_snap(ip, port, index)
def stack_thread(ip, port, index):
    grayscale_raw = listen.listen_for_stack(ip, port, index)



#Workflow test
snap_dict = workflow_gen.workflow_to_dict("Snapshot.txt")
snap_dict['Illumination Source'][laser_channel] = laser_setting
snap_dict['Start Position']['X (mm)'] = x_init
snap_dict['Start Position']['Y (mm)'] = y_init
snap_dict['Start Position']['Z (mm)'] = z_init
snap_dict['Start Position']['Angle (degrees)'] = r_init
workflow_gen.dict_to_workflow("currentSnapshot.txt", snap_dict)
# data_thread = threading.Thread(target=listen.listen_for_data(NUC_IP, PORT_LISTEN, 0))
# data_thread.start()
# data_thread.join()
# Start listening for data in a separate thread
t = threading.Thread(target=snap_thread, args=(NUC_IP, PORT_LISTEN, 0))
t.start()
tcpip_nuc.wf_to_nuc(client, "currentSnapshot.txt", c_workflow)
grayscale_image.show()
t.join()

# print(returnedData[1], returnedData[6])
#get current stage position
# returnedData = tcpip_nuc.command_to_nuc(client, c_snap)
# print("position")
# print(returnedData[6], returnedData[7], returnedData[8])

# Create a thread to listen for data
# data_thread = threading.Thread(target=listen.listen_for_data)

# # Start the thread
# data_thread.start()

# # Wait for the thread to finish
# data_thread.join()


#get clientID - listen on 53717, 128 bytes, figure out data structure from systemcommands.h controllerIDGET

#move to predefined stage position
#Xposition, data0 = 0 axis or x axis
returnedData = tcpip_nuc.command_to_nuc(client, c_setStagePos, data0 = 0, value = x_init)
returnedData = tcpip_nuc.command_to_nuc(client, c_setStagePos, data0 = 1, value = y_init)
returnedData = tcpip_nuc.command_to_nuc(client, c_setStagePos, data0 = 2, value = z_init)
returnedData = tcpip_nuc.command_to_nuc(client, c_setStagePos, data0 = 3, value = r_init)
tcpip_nuc.is_stage_stopped(client, c_StageStopCheck)


#getSnapshot
tcpip_nuc.wf_to_nuc(client, "currentSnapshot.txt", c_workflow)
tempImage=listen.listen_for_snap(NUC_IP, PORT_LISTEN, 0)
#returnedData = tcpip_nuc.command_to_nuc(client, c_snap)
#start loop
#Move half of a frame down from the current position
######how to determine half of a frame generically?######
images = []
coords = []
intensity_sum = np.sum(tempImage)
coords.append([x_init,y_init,z_init,r_init,intensity_sum])
# initialize the active coordinates
x = x_init
y = y_init
z = z_init
r = r_init

# send a command and listen for a response in a loop
wf_dict = workflow_gen.workflow_to_dict("ZStack.txt")
####
####maybe find max value for Y on instrument to set search range
for i in range(5):
    wf_dict['Start Position']['X (mm)'] = x
    wf_dict['Start Position']['Y (mm)'] = y+i*0.4
    wf_dict['Start Position']['Z (mm)'] = z
    wf_dict['Start Position']['Angle (degrees)'] = r
    wf_dict['End Position']['Y (mm)'] = y+i*0.4
    # Write a new workflow based on new Y positions
    workflow_gen.dict_to_workflow("currentZStack.txt", wf_dict)

    #function to check all 4 axes to check stage stop
    #take a Z stack that is N um thick (workflow or commands here??)
    tcpip_nuc.wf_to_nuc(client, "currentZStack.txt", c_workflow)
    ########################
    #find the most in focus slice
    ########################
    #calculate the Z position for that slice
    ######################
    #Take a snapshot there using the user defined laser and power
    zSnap = float(wf_dict['Start Position']['Z (mm)']) - float(wf_dict['End Position']['Z (mm)'])
    snap_dict = workflow_gen.workflow_to_dict("currentSnapshot.txt")
    snap_dict['Start Position']['Z (mm)'] = zSnap
    workflow_gen.dict_to_workflow("currentSnapshot.txt", snap_dict)
    tcpip_nuc.wf_to_nuc(client, "currentZStack.txt", c_workflow)
    tempImage = listen.listen_for_data(NUC_IP, PORT_LISTEN, i)
    #store coordinates and intensity sum in array 
    intensity_sum = np.sum(tempImage)
    coords.append([x_init,y_init,z_init,r_init,intensity_sum])
    #Loop may finish early if drastic maxima in intensity sum is detected
    intensity_sums = [coord[4] for coord in coords]
    if misc.check_maxima(intensity_sums):
        break
    #Loop must finish when max Y reached
client.close()