from functions.text_file_parsing import *
import shutil, time

plane_spacing = 10
framerate = 40.0032 #/s

def take_snapshot(connection_data, xyzr_init, visualize_event,  image_queue,
                  command_queue, send_event, laser_channel="Laser 3 488 nm", laser_setting='5.00 1'):
    
    wf_zstack= connection_data[2]

    #commands
    #Look in the functions/command_list.txt file for other command codes, or add more
    commands = text_to_dict('functions/command_list.txt')
    COMMAND_CODES_CAMERA_WORK_FLOW_START  = int(commands['CommandCodes.h']['COMMAND_CODES_CAMERA_WORK_FLOW_START'] )
    ##############################
    #Brightfield image to verify sample holder location
    snap_dict = workflow_to_dict("workflows/"+wf_zstack)
    snap_dict = dict_to_snap(snap_dict, xyzr_init,framerate, plane_spacing)
    snap_dict = laser_or_LED(snap_dict, laser_channel, laser_setting, laser_on = True)
    snap_dict = dict_comment(snap_dict, 'GUI Snapshot')


    #For troubleshooting, double check a recent snapshot workflow
    dict_to_workflow("workflows/currentSnapshot.txt", snap_dict)

    #WORKFLOW.TXT FILE IS ALWAYS USED FOR send_event, other workflow files are backups and only used to validate steps
    shutil.copy("workflows/currentSnapshot.txt", 'workflows/workflow.txt')

 
    #take a snapshot
    command_queue.put(COMMAND_CODES_CAMERA_WORK_FLOW_START )
    send_event.set()
    #only close out the connections once the final image is collected
    visualize_event.set()
    image=image_queue.get()
    print(image.shape)
    #Clean up "delete" PNG files or dont make them
    ###

