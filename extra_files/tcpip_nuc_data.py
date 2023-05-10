import socket
import os
import sys

import numpy as np

import struct



def command_to_nuc(FLAMINGO_IP, FLAMINGO_PORT, wf_file, command, wf = True):

    '''

    function to send a workflow file to nuc and start the workflow

    :param FLAMINGO_IP: ip address of NUC

    :param FLAMINGO_PORT: Port 53717

    :param wf_file: path to workflow file

    :param command: 4136 to start a workflow (microscope control software verison dependent)

    :param wf: whether a workflow file is sent of not

    :return: nothing

    '''
    print("socket connect")
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    print(client)
    client.connect((FLAMINGO_IP, FLAMINGO_PORT))

    if wf:

        fileBytes = os.path.getsize(wf_file)

    else:

        fileBytes = 0


    #print(fileBytes)
    cmd_start = np.uint32(0xF321E654)  # start command

    cmd = np.uint32(command) #cmd command (CommandCodes.h): open in Visual Studio Code + install C/C++ Extension IntelliSense, when hovering over command binary number visible

    status = np.int32(0)

    hardwareID = np.int32(0)

    subsystemID = np.int32(0)

    clientID = np.int32(0)

    int32Data0 = np.int32(0) #e.g. for stage movement: 0 == x-axis, 1 == y.axis, 2 == z.axis, 3 = rotational axis

    int32Data1 = np.int32(0)

    int32Data2 = np.int32(0)

    cmdDataBits0 = np.int32(0)

    doubleData = float(0) #e.g. 64-bits, values of the end-position of the axis

    addDataBytes = np.int32(fileBytes) # only if you sent a workflow file, else fileBytes = 0

    buffer_72 = b'\0' * 72

    cmd_end = np.uint32(0xFEDC4321)  # end command



    s = struct.Struct('I I I I I I I I I I d I 72s I') # pack everything to binary via struct

    scmd = s.pack(cmd_start, cmd, status, hardwareID,

                  subsystemID, clientID, int32Data0,

                  int32Data1, int32Data2, cmdDataBits0,

                  doubleData, addDataBytes, buffer_72, cmd_end)



    try:

        client.send(scmd)

        if wf:

            workflow_file = open(wf_file).read()

            client.send(workflow_file.encode())

        msg = client.recv(128)

        received = s.unpack(msg)

        addData = client.recv(received[11]) # unpack additional data sent by the nuc

    except socket.error:

        print('Failed to send data')

    client.close()





if __name__ == 'main':

    command_to_nuc('10.129.37.17', 53717, "40StackWorkflow.txt", 4137, wf = True)
    print("Test")

