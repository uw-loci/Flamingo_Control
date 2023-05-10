import tcpip_nuc
import workflow_gen
import misc
import socket
import struct
from PIL import Image, ImageOps
from queue import Queue
import socket
import numpy as np
import select
import tcpip_nuc
import time
index = 0
def empty_socket(sock):
    """remove the data present on the socket"""
    input = [sock]
    while 1:
        sock.setblocking(0)
        inputready, o, e = select.select(input,[],[], 0.0)
        print("Data amount waiting " +str(len(inputready)))
        if len(inputready)==0: break
        for s in inputready: s.recv(1)
    print(f'socket {sock} clear')
    sock.setblocking(1)

def bytes_waiting(sock):
    # Use select() to check if there is data waiting to be read
    # on the socket.
    r, _, _ = select.select([sock], [], [], 0)
    if r:
        # If there is data waiting, use the recv() method with
        # MSG_PEEK flag to peek at the first byte of data and get
        # the total number of bytes waiting to be read.
        sock.setblocking(0)
        data = len(sock.recv(80000, socket.MSG_PEEK))
        sock.setblocking(1)
        #print(f"length of data {data}")
        return data
    else:
        # If there is no data waiting, return 0.
        return 0



def command_listen_thread(client, idle_state, terminate_event, c_idle_state):
    print('LISTENING for commands on ' +str(client))
    empty_socket(client)
    s = struct.Struct('I I I I I I I I I I d I 72s I') # pack everything to binary via struct
    while not terminate_event.is_set():
        while True:

            msg = client.recv(128)
            if len(msg) != 128:
                break
            #print(len(msg))
            received = s.unpack(msg)
            #print(f"Received on 53717: {received[1]} : {received[2]} : {received[3]} : {received[6]} : {received[10]} : size {received[11]}")
            #need to check 53717 for received[1] being idle
            #print("listening to 53717 got " + str(received[1]))
            if received[1] == c_idle_state:
                print('status idle: '+str(received[2]))
                if received[2] == 1:                    
                    idle_state.set()

            # data_waiting = 1
            # while data_waiting != 128 and data_waiting!=0:
            #     data_waiting = bytes_waiting(client)
            #     print('data waiting: '+str(data_waiting))
            #     if data_waiting != 128 and data_waiting!=0:
            #         altmsg = client.recv(data_waiting)
            #         print("Data received on command listen that was not 128 or 0 bytes: " + str(len(altmsg)))

def live_listen_thread(live_client, terminate_event, processing_event, image_queue):
    global index
    print('LISTENING for image data on ' +str(live_client))
    while not terminate_event.is_set():
        try:
            # receive the header
            print("Waiting for image data")
            header_data = live_client.recv(40)
            #make sure the queue is empty for a new in focus Z slice position
            print('header')
            if len(header_data) != 40:
                raise ValueError(f'Header length should be 40 bytes, not {len(header_data)}')
            
            # parse the header
            print("parsing header, entering acquisition")
            header = struct.unpack('I I I I I I I I I I', header_data)
            image_size, image_width, image_height= header[0], header[1], header[2]

            #get the stack size from the workflow file, as it is not sent as part of the header information
            current_workflow_dict = workflow_gen.workflow_to_dict('workflows/workflow.txt')
            stack_size = float(current_workflow_dict['Stack Settings']['Number of planes'])
            MIP = current_workflow_dict['Experiment Settings']['Display max projection']

            # receive the image data
            # Coule probably be condensed with some better preparation
            if MIP == "true" or stack_size == 1:
                #print(f'MIP is {MIP}')
                #Single image from snapshot workflows
                image_data = b''
                while len(image_data) < image_size:
                    data = live_client.recv(image_size - len(image_data))
                    if not data:
                        raise socket.error('Incomplete image data')
                    image_data += data
                image = Image.frombytes('I;16', (image_width, image_height), image_data)
                rotated_image = image.rotate(90, expand=True)
                rotated_image.save(f'output_png/output{index}.png')
                index = index+1
                ################################33
                grayscale_image = rotated_image.convert("L")
                #grayscale_image.show()
                
                # return the grayscale image
                #store intensity sum 
                image_queue.put(np.array(grayscale_image))
                print('processing set')
                processing_event.set()


            else: 
                print("entering stack handling stack size: "+str(stack_size))
                images = []
                #count down through stack, getting each image and adding it to images
                #finally, place whole stack in the image_queue
                while stack_size > 0:
                    image_data = b''
                    while len(image_data) < image_size:
                        data = live_client.recv(image_size - len(image_data))
                        if not data:
                            raise socket.error('Incomplete image data')
                        image_data += data
                    print('image data received, plane ' + str(stack_size))
                    # convert and save the image
                    # convert the image data to a PIL image object
                    image = Image.frombytes('I;16', (image_width, image_height), image_data)
                    
                    # rotate the image and append it to the list of images
                    rotated_image = image.rotate(90, expand=True)
                    images.append(rotated_image)
                    stack_size=stack_size-1
                    #strip off header
                    if stack_size != 0:
                        header_data = live_client.recv(40)

                # combine the images into a single 3D array
                stack = np.stack([np.array(image) for image in images])
                print(stack.shape)
                # save the stack as a numpy array
                np.save(f'output_npy/output{index}.npy', stack)
                image_queue.put(stack)
                print('processing set')
                processing_event.set()
            print("looping")

            
        except socket.error as e:
            print(f'Socket error: {e}')
            live_client.close()
            break
            
        except Exception as e:
            print(f'Error: {e}')
            continue
    print('I guess termination')
    return None

def send_thread(client,  command_queue, send_event, system_idle, c_workflow, data0_queue, data1_queue, data2_queue, value_queue):
    while True:
        #only go when an event is set
        send_event.wait()
        print('send event triggered')
        #acquire_event.wait()
        command = command_queue.get()

        system_idle.clear()

        if command == c_workflow:
            print("Sending workflow to nuc")
            tcpip_nuc.wf_to_nuc(client, 'workflows/workflow.txt', c_workflow)
            send_event.clear()
        else: #Handle commands
            print('Send non-workflow command to nuc: ' +str(command))
            #make sure the queues are not empty, if they are use the default value of 0
            if not data0_queue.empty():
                data0=data0_queue.get()
            else:
                data0=0
            if not data1_queue.empty():
                data1=data1_queue.get()
            else:
                data1=0
            if not data2_queue.empty():
                data2=data2_queue.get()
            else:
                data2=0
            if not value_queue.empty():
                value=value_queue.get()
            else:
                value=0    
            print(f'command to nuc uses command {command}, data0 {data0}, data1 {data1}, data2 {data2}, value {value}')            
            tcpip_nuc.command_to_nuc(client, command, data0, data1,data2,value)
            send_event.clear()
        #returnedData = tcpip_nuc.command_to_nuc(client, 4119)
        #need to check 53717 for received[1] being a "idle" code 36874



def processing_thread(z_plane_queue, terminate_event, processing_event, intensity_queue, image_queue):
    while True:
        if terminate_event.is_set():
            break
        processing_event.wait()
        print('processing thread active')
        #determine what type of event to process
        #Needs to be made more generic
        image_data=image_queue.get()

        #maybe both results could just be "result_queue"
        if len(image_data.shape) == 2:
            intensity_sum = np.sum(image_data)
            print(f'intensity sum: {intensity_sum}')
            intensity_queue.put(intensity_sum)
            processing_event.clear()        
        else:
            #process to find the most in focus of a stack
            #Possibly add a function for the Discrete Cosine Transform?
            #using IF this could probably just be the max again, but it would be nice to see this work

            z_plane_queue.put(misc.find_most_in_focus_plane(image_data))
            processing_event.clear()


