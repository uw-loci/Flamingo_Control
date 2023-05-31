from functions.text_file_parsing import *
from functions.microscope_connect import get_microscope_settings
import time, os
from threading import Event
from queue import Queue

def set_home(connection_data: list, xyzr:list, command_queue:Queue, other_data_queue:Queue,send_event:Event):
    nuc_client, live_client, wf_zstack, LED_on, LED_off = connection_data
    commands = text_to_dict(os.path.join('src','py2flamingo','functions','command_list.txt'))
    COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD = int(commands['CommandCodes.h']['COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD'] )
    COMMAND_CODES_COMMON_SCOPE_SETTINGS_SAVE = int(commands['CommandCodes.h']['COMMAND_CODES_COMMON_SCOPE_SETTINGS_SAVE'] )
    print('load settings')
    print(COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD)

    #get microscope settings file to temp location
    command_queue.put(COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD) #movement
    send_event.set()
    while not command_queue.empty():
        time.sleep(.3)

    #microscope settings should now be in a text file called ScopeSettings.txt in the 'workflows' directory
    #convert them into a dict to extract useful information
    #########
    settings_dict = text_to_dict(os.path.join('microscope_settings','ScopeSettings.txt'))

    settings_dict['Stage limits']['Home x-axis'] = xyzr[0]
    settings_dict['Stage limits']['Home y-axis'] = xyzr[1]
    settings_dict['Stage limits']['Home z-axis'] = xyzr[2]
    settings_dict['Stage limits']['Home r-axis'] = xyzr[3]
    dict_to_text(os.path.join('microscope_settings','send_settings.txt'), settings_dict)
    print('save settings')
    
    command_queue.put(COMMAND_CODES_COMMON_SCOPE_SETTINGS_SAVE )
    send_event.set()
    time.sleep(0.1)
    #remove sent text file
