import socket
import os
import sys
import time
import numpy as np

import struct



def wf_to_nuc(client, wf_file, command):

    '''

    function to send a workflow file to nuc and start the workflow

    :param FLAMINGO_IP: ip address of NUC

    :param FLAMINGO_PORT: Port 53717

    :param wf_file: path to workflow file

    :param command: 4136 to start a workflow (microscope control software verison dependent)

    :param wf: whether a workflow file is sent of not

    :return: nothing

    '''

    fileBytes = os.path.getsize(wf_file)
    print(fileBytes)


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

        workflow_file = open(wf_file).read()

        client.send(workflow_file.encode())

        msg = client.recv(128)

        received = s.unpack(msg)
        return received
        #addData = client.recv(received[11]) # unpack additional data sent by the nuc

    except socket.error:

        print('Failed to send data')


def command_to_nuc(client,command, data0=0, data1=0, data2=0, value=0.0):

    '''

    function to send a workflow file to nuc and start the workflow

    :param FLAMINGO_IP: ip address of NUC

    :param FLAMINGO_PORT: Port 53717

    :param wf_file: path to workflow file

    :param command: 4136 to start a workflow (microscope control software verison dependent)

    :param wf: whether a workflow file is sent of not

    :return: nothing

    '''

    fileBytes = 0

    cmd_start = np.uint32(0xF321E654)  # start command

    cmd = np.uint32(command) #cmd command (CommandCodes.h): open in Visual Studio Code + install C/C++ Extension IntelliSense, when hovering over command binary number visible

    status = np.int32(0)

    hardwareID = np.int32(0)

    subsystemID = np.int32(0)

    clientID = np.int32(0)

    int32Data0 = np.int32(data0) #e.g. for stage movement: 0 == x-axis, 1 == y.axis, 2 == z.axis, 3 = rotational axis

    int32Data1 = np.int32(data1)

    int32Data2 = np.int32(data2)

    cmdDataBits0 = np.int32(0)

    doubleData = float(value) #e.g. 64-bits, values of the end-position of the axis

    addDataBytes = np.int32(fileBytes) # only if you sent a workflow file, else fileBytes = 0

    buffer_72 = b'\0' * 72

    cmd_end = np.uint32(0xFEDC4321)  # end command



    s = struct.Struct('I I I I I I I I I I d I 72s I') # pack everything to binary via struct

    scmd = s.pack(cmd_start, cmd, status, hardwareID,

                  subsystemID, clientID, int32Data0,

                  int32Data1, int32Data2, cmdDataBits0,

                  doubleData, addDataBytes, buffer_72, cmd_end)


    print('before try')
    try:

        client.send(scmd)
        print('before receive')
        msg = client.recv(128)
        print('after receive')
        received = s.unpack(msg)

        addData = client.recv(received[11]) # unpack additional data sent by the nuc
        return received
    except socket.error:

        print('Failed to send data')


def is_stage_stopped(client, c_StageStopCheck):
    all_true = False
    xb, yb, zb, rb = False
    while not all_true:
        if not xb:
            xb = command_to_nuc(client, c_StageStopCheck, data0 = 0)[1] #second entry should be "status", with 0 indicating not finished
        if not yb:
            yb = command_to_nuc(client, c_StageStopCheck, data0 = 1)[1]
        if not zb:
            zb = command_to_nuc(client, c_StageStopCheck, data0 = 2)[1]
        if not rb:
            rb = command_to_nuc(client, c_StageStopCheck, data0 = 3)[1]
        all_true = xb*yb*rb*zb #if any value isn't 1, all_true stays 0/False
        time.sleep(0.5)


if __name__ == 'main':
    print("what")
    wf_to_nuc('10.129.37.17', 53717, "40StackWorkflow.txt", 12292, wf = True)
    print("Test")

