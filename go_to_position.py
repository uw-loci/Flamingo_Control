from functions.microscope_connect import go_to_XYZR
from functions.text_file_parsing import text_to_dict
import os

def go_to_position(xyzr, command_data_queue, command_queue, send_event):
    #Look in the functions/command_list.txt file for other command codes, or add more
    commands = text_to_dict(os.path.join('functions','command_list.txt'))
    
    COMMAND_CODES_STAGE_POSITION_SET  = int(commands['CommandCodes.h']['COMMAND_CODES_STAGE_POSITION_SET'])
    go_to_XYZR(command_data_queue, command_queue, send_event, xyzr)

