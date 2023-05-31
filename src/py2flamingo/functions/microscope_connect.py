import os
import socket
import time
from threading import Event, Thread
from functions.threads import command_listen_thread, processing_thread, send_thread, live_listen_thread
from functions.text_file_parsing import text_to_dict
import tkinter as tk
from tkinter import messagebox
from typing import Tuple
#Default values for LED on and off
LED_off = '00.00 0'
LED_on = '50.0 1'
#commands
commands = text_to_dict(os.path.join('src','py2flamingo','functions','command_list.txt'))
COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD = int(commands['CommandCodes.h']['COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD'] )
COMMAND_CODES_COMMON_SCOPE_SETTINGS  = int(commands['CommandCodes.h']['COMMAND_CODES_COMMON_SCOPE_SETTINGS'])
COMMAND_CODES_CAMERA_WORK_FLOW_START  = int(commands['CommandCodes.h']['COMMAND_CODES_CAMERA_WORK_FLOW_START'] )
COMMAND_CODES_STAGE_POSITION_SET  = int(commands['CommandCodes.h']['COMMAND_CODES_STAGE_POSITION_SET'])
COMMAND_CODES_SYSTEM_STATE_IDLE  = int(commands['CommandCodes.h']['COMMAND_CODES_SYSTEM_STATE_IDLE'])
COMMAND_CODES_CAMERA_PIXEL_FIELD_Of_VIEW_GET  = int(commands['CommandCodes.h']['COMMAND_CODES_CAMERA_PIXEL_FIELD_Of_VIEW_GET'])
COMMAND_CODES_CAMERA_IMAGE_SIZE_GET  = int(commands['CommandCodes.h']['COMMAND_CODES_CAMERA_IMAGE_SIZE_GET'])
    ##############################



def go_to_XYZR(command_data_queue, command_queue, send_event, xyzr:Tuple[float, float, float, float]):
    # Unpack the provided XYZR coordinates, r is in degrees, other values are in mm
    x,y,z,r = xyzr
    print(f'moving to {x} {y} {z} {r}')
    #data0_queue.put(0) doesn't represent a motion axis
    # Put X-axis movement command data in the queue
    command_data_queue.put([1,0,0,x])# 1 = xaxis
    # Put movement command in the queue
    command_queue.put(COMMAND_CODES_STAGE_POSITION_SET ) #movement
    # Signal the send event to trigger command sending
    send_event.set()
    # Wait until the command queue is empty (indicating the command has been sent off to the controller)
    while not command_queue.empty():
        time.sleep(.1)

    command_data_queue.put([3,0,0,z])# 3 = zaxis
    command_queue.put(COMMAND_CODES_STAGE_POSITION_SET ) #movement
    send_event.set()
    while not command_queue.empty():
        time.sleep(.1)

    command_data_queue.put([4,0,0,r])# 4 = rotation
    command_queue.put(COMMAND_CODES_STAGE_POSITION_SET ) #movement
    send_event.set()
    while not command_queue.empty():
        time.sleep(.1)

    command_data_queue.put([2,0,0,y])# 2 = yaxis
    command_queue.put(COMMAND_CODES_STAGE_POSITION_SET ) #movement
    send_event.set()
    while not command_queue.empty():
        time.sleep(.1)


def get_microscope_settings(command_queue, other_data_queue, send_event):
    command_queue.put(COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD) #movement
    send_event.set()
    while not command_queue.empty():
        time.sleep(.3)

    #microscope settings should now be in a text file called ScopeSettings.txt in the 'workflows' directory
    #convert them into a dict to extract useful information
    #########
    scope_settings = text_to_dict('microscope_settings/ScopeSettings.txt')

    # objective_mag = float(metadata_settings['Instrument']['Type']['Objective lens magnification'])
    # tube_lens_design_length = float(metadata_settings['Instrument']['Type']['Tube lens design focal length (mm)'])
    # tube_lens_actual_length = float(metadata_settings['Instrument']['Type']['Tube lens length (mm)'])
    # #######################################################
    command_queue.put(COMMAND_CODES_CAMERA_PIXEL_FIELD_Of_VIEW_GET ) #movement
    send_event.set()
    while not command_queue.empty():
        time.sleep(.3)
    #FIX HARDCODED 0.65
    image_pixel_size = other_data_queue.get()
    return image_pixel_size, scope_settings

def start_connection(NUC_IP:str, PORT_NUC:int):
    PORT_LISTEN = PORT_NUC+1
    #Workflow templates
    #Current code requires EITHER Display max projection OR Work flow live view enabled
    #but not both
    wf_zstack = 'ZStack.txt' #Fluorescent Z stack to find sample
    ########
    #Function specific, validate that Snapshot.txt and ZStack.txt exist, or find way to make sure they don't need to exist
    try:
        nuc_client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        nuc_client.settimeout(2)
        nuc_client.connect((NUC_IP, PORT_NUC))
 
        live_client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        live_client.connect((NUC_IP, PORT_LISTEN))
    except socket.timeout:
        # Handle the connection timeout and show a popup message
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo('Connection Error', 'Check that you have network access to the microscope. This may also be an IT issue. Close the program and try again.')
        exit()
    except ConnectionRefusedError:
    # Handle the connection error and show a popup message
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo('Connection Error', 'Connection was refused.')

    #print('listener thread socket created on ' + str(IP_REMOTE)+ ' : '+str(PORT_REMOTE))
    #########CONNECTION END ###############
    return nuc_client, live_client, wf_zstack, LED_on, LED_off

def create_threads(nuc_client:socket, live_client:socket, other_data_queue=None, image_queue=None, command_queue=None, z_plane_queue=None,
                   intensity_queue=None, visualize_queue=None, system_idle=None, processing_event=None, send_event=None,
                   terminate_event=None, command_data_queue=None, stage_location_queue=None):
    # Create the image processing thread
    processing_thread_var = Thread(target=processing_thread, 
                                   args=(z_plane_queue, terminate_event, processing_event, intensity_queue, image_queue))
    
    # Create the send thread to send individual commands and workflows to the microscope control software
    send_thread_var = Thread(target=send_thread, 
                             args=(nuc_client, command_queue, send_event, system_idle, command_data_queue))
    
    # Create the command listen thread to listen to responses from the microscope about its status
    command_listen_thread_var = Thread(target=command_listen_thread, 
                                       args=(nuc_client, system_idle, terminate_event, other_data_queue))
    
    # Create the live listen thread to receive image data sent to the 'live' view
    live_listen_thread_var = Thread(target=live_listen_thread, 
                                    args=(live_client, terminate_event, image_queue, visualize_queue))
    
    # Set daemon flag for threads (optional)
    live_listen_thread_var.daemon = True
    send_thread_var.daemon = True
    command_listen_thread_var.daemon = True
    processing_thread_var.daemon = True
    
    # Start the threads
    live_listen_thread_var.start()
    command_listen_thread_var.start()
    send_thread_var.start()
    processing_thread_var.start()
    
    # Return the thread variables for potential later use
    return live_listen_thread_var, command_listen_thread_var, send_thread_var, processing_thread_var


def close_connection(nuc_client:socket, live_client:socket,live_listen_thread_var:Thread, command_listen_thread_var:Thread,send_thread_var:Thread,processing_thread_var:Thread):
    #Join may no longer be necessary due to use of daemon=true?
    send_thread_var.join()
    live_listen_thread_var.join()
    command_listen_thread_var.join()
    processing_thread_var.join()
    nuc_client.close()
    live_client.close()