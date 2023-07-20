# Communicate directly with the nuc by way of workflows and commands
# These workflows and commands will then be processed by the nuc and used to control the microscope hardware
# Courtesy of Gesine Fiona Müller from the Huisken lab.

import os
import socket
import struct
import sys

import numpy as np


# This used to be one function instead of two and could still go back. It was easier to understand as two, for me.
def text_to_nuc(client: socket, wf_file: str, command: int):
    """

    function to send a workflow file to nuc and start the workflow

    :param client: socket to connect to the nuc

    :param wf_file: path to workflow file

    :param command: 4136 to start a workflow (microscope control software verison dependent)

    :return: nothing

    """

    # print(f"workflow command is {command}")
    fileBytes = os.path.getsize(wf_file)
    # print(fileBytes)

    cmd_start = np.uint32(0xF321E654)  # start command [0]
    # cmd_start = bytes(0xF321E654)
    cmd = np.uint32(
        command
    )  # [1] cmd command (CommandCodes.h): open in Visual Studio Code + install C/C++ Extension IntelliSense, when hovering over command binary number visible

    status = np.int32(0)  # [2]

    hardwareID = np.int32(0)  # [3]

    subsystemID = np.int32(0)  # [4]

    clientID = np.int32(0)  # [5]

    int32Data0 = np.int32(
        0
    )  # [6]  e.g. for stage movement: 1 == x-axis, 2 == y.axis, 3 == z.axis, 4 = rotational axis

    int32Data1 = np.int32(0)  # [7]

    int32Data2 = np.int32(0)  # [8]

    cmdDataBits0 = np.int32(1)  # [9]

    doubleData = float(0)  # #[10] e.g. 64-bits, values of the end-position of the axis

    addDataBytes = np.int32(
        fileBytes
    )  # [11] only if you sent a workflow file, else fileBytes = 0

    buffer_72 = b"\0" * 72

    cmd_end = np.uint32(0xFEDC4321)  # end command

    s = struct.Struct(
        "I I I I I I I I I I d I 72s I"
    )  # pack everything to binary via struct

    scmd = s.pack(
        cmd_start,
        cmd,
        status,
        hardwareID,
        subsystemID,
        clientID,
        int32Data0,
        int32Data1,
        int32Data2,
        cmdDataBits0,
        doubleData,
        addDataBytes,
        buffer_72,
        cmd_end,
    )

    try:
        # print(command)
        # print(wf_file)
        # workflow_file = open(wf_file, encoding='utf-8').read()
        workflow_file = open(wf_file, "rb").read()
        # print('sending')
        client.send(scmd)
        # print(len(scmd))
        client.send(workflow_file)
        # print("Text file sent")

        return
        # addData = client.recv(received[11]) # unpack additional data sent by the nuc

    except socket.error:
        print("Failed to send data wf")


# Commands to nuc, requires additional input values, data#, and does not require a workflow file
def command_to_nuc(client: socket, command: int, command_data=[0, 0, 0, 0.0]):
    """

    function to send a workflow file to nuc and start the workflow

    :param FLAMINGO_IP: ip address of NUC

    :param FLAMINGO_PORT: Port 53717

    :param wf_file: path to workflow file

    :param command: 4136 to start a workflow (microscope control software verison dependent)

    :param wf: whether a workflow file is sent of not

    :return: nothing

    """

    # if no command_data is provided, these can be 0
    data0, data1, data2, value = command_data
    cmd_start = np.uint32(0xF321E654)  # start command

    cmd = np.uint32(
        command
    )  # cmd command (CommandCodes.h): open in Visual Studio Code + install C/C++ Extension IntelliSense, when hovering over command binary number visible

    status = np.int32(0)

    hardwareID = np.int32(0)

    subsystemID = np.int32(0)

    clientID = np.int32(0)

    int32Data0 = np.int32(
        data0
    )  # e.g. for stage movement: 0 == x-axis, 1 == y.axis, 2 == z.axis, 3 = rotational axis

    int32Data1 = np.int32(data1)

    int32Data2 = np.int32(data2)

    cmdDataBits0 = np.uint32(0x80000000)  # 0x80000000

    doubleData = float(value)  # e.g. 64-bits, values of the end-position of the axis

    addDataBytes = np.int32(0)  # only if you sent a workflow file, else fileBytes = 0

    buffer_72 = b"\0" * 72

    cmd_end = np.uint32(0xFEDC4321)  # end command

    s = struct.Struct(
        "I I I I I I I I I I d I 72s I"
    )  # pack everything to binary via struct

    scmd = s.pack(
        cmd_start,
        cmd,
        status,
        hardwareID,
        subsystemID,
        clientID,
        int32Data0,
        int32Data1,
        int32Data2,
        cmdDataBits0,
        doubleData,
        addDataBytes,
        buffer_72,
        cmd_end,
    )

    # print('before try')
    try:
        client.send(scmd)
        return  # received
    except socket.error as e:
        print("cmd to nuc - Failed to send data", e)
