import os
import socket
import time
from threading import Event, Thread
from functions.threads import command_listen_thread, processing_thread, send_thread, live_listen_thread
import functions.text_file_parsing
import tkinter as tk
from tkinter import messagebox

#Need a way to get this information from the user
CAMERA_PIXEL_SIZE = 0.65
NUC_IP = '10.129.37.17' #From Connection tab in GUI
PORT_NUC = 53717 #From Connection tab in GUI
PORT_LISTEN = PORT_NUC+1 #Live mode data, on 53718
#There should also be an associated initial x,y,z,r 


def go_to_XYZR(data0_queue, data1_queue, data2_queue, value_queue, command_queue, send_event, c_setStagePos, x,y,z,r):
    print(f"moving to {x} {y} {z} {r}")
    #data0_queue.put(0) doesn't represent a motion axis
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


def get_microscope_settings(command_queue,other_data_queue, c_scope_settings_request, c_pixel_size, send_event):
    command_queue.put(c_scope_settings_request) #movement
    send_event.set()
    while not command_queue.empty():
        time.sleep(.3)

    #microscope settings should now be in a text file called ScopeSettings.txt in the 'workflows' directory
    #convert them into a dict to extract useful information
    #########

    #Not currently used
    metadata_settings = functions.text_file_parsing.text_to_dict("microscope_settings/FlamingoMetaData.txt")
    scope_settings = functions.text_file_parsing.text_to_dict("microscope_settings/ScopeSettings.txt")
    ################
    #Occasionally there is an error on this next step, not entirely sure why. Inconsistent.
    #######
    objective_mag = float(metadata_settings['Instrument']['Type']['Objective lens magnification'])
    tube_lens_design_length = float(metadata_settings['Instrument']['Type']['Tube lens design focal length (mm)'])
    tube_lens_actual_length = float(metadata_settings['Instrument']['Type']['Tube lens length (mm)'])
    #######################################################
    command_queue.put(c_pixel_size) #movement
    send_event.set()
    while not command_queue.empty():
        time.sleep(.3)
    #FIX HARDCODED 0.65
    image_pixel_size = other_data_queue.get()
    return image_pixel_size, scope_settings

def start_connection():
    LED_off = '00.00 0'
    LED_on = '38.04 1'
     #Depth of Z stack to search for sample in mm

    #Workflow templates
    #Current code requires EITHER Display max projection OR Work flow live view enabled
    #but not both
    wf_zstack = "ZStack.txt" #Fluorescent Z stack to find sample
    wf_snapshot = "Snapshot.txt"


    ########
    if not os.path.exists("workflows"):
        os.makedirs("workflows")
    if not os.path.exists("ouput_png"):
        os.makedirs("ouput_png")

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
        messagebox.showinfo("Connection Error", "Check that you have network access to the microscope. This may also be an IT issue. Close the program and try again.")
        exit()
    except ConnectionRefusedError:
    # Handle the connection error and show a popup message
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("Connection Error", "Connection was refused.")

    #print('listener thread socket created on ' + str(IP_REMOTE)+ ' : '+str(PORT_REMOTE))
    #########CONNECTION END ###############
    return nuc_client, live_client, wf_snapshot, wf_zstack, LED_on, LED_off

    ########DISCOVER INSTRUMENT ID#######
    #######SET VARIABLES BASED ON INSTRUMENT#
    #################################

def create_threads(c_pixel_size, c_scope_settings_returned,c_idle_state,c_workflow, nuc_client, live_client,other_data_queue, image_queue, command_queue, z_plane_queue, intensity_queue, visualize_queue, view_snapshot, system_idle, processing_event, send_event, terminate_event, data0_queue, data1_queue, data2_queue, value_queue, stage_location_queue):
    processing_thread_var = Thread(target=processing_thread, 
                            args=(z_plane_queue, terminate_event, processing_event, intensity_queue, image_queue))
    send_thread_var = Thread(target=send_thread, 
                            args=(nuc_client, command_queue, send_event, system_idle, c_workflow, data0_queue, data1_queue, data2_queue, value_queue))
    command_listen_thread_var = Thread(target=command_listen_thread, 
                            args=(nuc_client, system_idle, terminate_event, c_idle_state, c_scope_settings_returned, c_pixel_size, other_data_queue))
    live_listen_thread_var = Thread(target=live_listen_thread, 
                            args=(live_client, terminate_event, image_queue, visualize_queue))
    live_listen_thread_var.daemon = True
    send_thread_var.daemon = True
    command_listen_thread_var.daemon = True
    processing_thread_var.daemon = True
    
    live_listen_thread_var.start()
    command_listen_thread_var.start()
    send_thread_var.start()
    processing_thread_var.start()
 
    return live_listen_thread_var, command_listen_thread_var,send_thread_var,processing_thread_var

def close_connection(nuc_client, live_client,live_listen_thread_var, command_listen_thread_var,send_thread_var,processing_thread_var):
    #Join may no longer be necessary due to use of daemon=true?
    send_thread_var.join()
    live_listen_thread_var.join()
    command_listen_thread_var.join()
    processing_thread_var.join()
    nuc_client.close()
    live_client.close()