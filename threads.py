import tcpip_nuc
import workflow_gen
import misc
import socket
import struct
import threading
from PIL import Image, ImageOps
from queue import Queue
import socket
import numpy as np
import select

def empty_socket(sock):
    """remove the data present on the socket"""
    input = [sock]
    while 1:
        sock.setblocking(0)
        inputready, o, e = select.select(input,[],[], 0.0)
        if len(inputready)==0: break
        for s in inputready: s.recv(1)
    print('socket clear')
    sock.setblocking(1)



image_queue = Queue()
index = 0
def listener_thread(NUC_IP,PORT_LISTEN, terminate_event, processing_event, intensity_queue):
    # Create a socket object
    client_listen = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Connect to the remote server
    client_listen.connect((NUC_IP, PORT_LISTEN))
    global index

    while not terminate_event.is_set():
        try:
            # receive the header
            print('LISTENING on ' +str(client_listen))
            header_data = client_listen.recv(40)
            #make sure the queue is empty for a new in focus Z slice position
            print('header')
            if len(header_data) != 40:
                raise ValueError(f'Header length should be 40 bytes, not {len(header_data)}')
            
            # parse the header
            print("listen thread header received")
            header = struct.unpack('I I I I I I I I I I', header_data)
            image_size, image_width, image_height, start_index, stop_index = header[0], header[1], header[2], header[8], header[9]
            stack_size = stop_index - start_index
            print('stack size '+ str(stack_size))
            # receive the image data
            image_data = b''
            while len(image_data) < image_size:
                data = client_listen.recv(image_size - len(image_data))
                if not data:
                    raise socket.error('Incomplete image data')
                image_data += data
            print('image data received')
            print(len(image_data))
            # convert and save the image

            images = []
            if stack_size > 1:
                for i in range(stack_size):
                    image_data = b''
                    while len(image_data) < image_size:
                        data = client_listen.recv(image_size - len(image_data))
                        if not data:
                            raise socket.error('Incomplete image data')
                        image_data += data
                    
                    # convert the image data to a PIL image object
                    image = Image.frombytes('I;16', (image_width, image_height), image_data)
                    
                    # rotate the image and append it to the list of images
                    rotated_image = image.rotate(90, expand=True)
                    images.append(rotated_image)

                # combine the images into a single 3D array
                stack = np.stack([np.array(image) for image in images])
                print(stack.shape())
                # save the stack as a numpy array
                np.save(f'output{index}.npy', stack)
                image_queue.put(stack)
                print('processing set')
                processing_event.set()
                stack_size = -1
            else:
                #Single image from snapshot workflows
                image = Image.frombytes('I;16', (image_width, image_height), image_data)
                rotated_image = image.rotate(90, expand=True)
                rotated_image.save(f'output{index}.png')
                index = index+1
                grayscale_image = rotated_image.convert("L")
                grayscale_image.show()
                
                # return the grayscale image
                #store intensity sum 
                print('intensity summing')
                intensity_sum = np.sum(grayscale_image)
                intensity_queue.put(intensity_sum)
            
        except socket.error as e:
            print(f'Socket error: {e}')
            client_listen.close()
            break
            
        except Exception as e:
            print(f'Error: {e}')
            continue
    print('I guess termination')
    return None

def workflow_thread(client, ymax, c_workflow, terminate_event, acquire_event):
    while True:
        acquire_event.wait()
        print('Checking Y position')
        dict = workflow_gen.workflow_to_dict('workflows/workflow.txt')
        #####################
        #check Y value against Ymax, if greater than, exit
        if float(dict['Start Position']['Y (mm)']) > ymax:
            print("YMax reached, terminating all threads")
            terminate_event.set()
            break
        print("Sending workflow to nuc")
        received = tcpip_nuc.wf_to_nuc(client, 'workflows/workflow.txt', c_workflow)
        #returnedData = tcpip_nuc.command_to_nuc(client, 4119)
        print("received in workflow thread "+ str(received[2]))
        acquire_event.clear()

        ############
def processing_thread(z_plane_queue, terminate_event, processing_event):
    while True:
        if terminate_event.is_set():
            break
        if  processing_event.is_set():
            print('processing thread active')
            #process to find the most in focus of a stack
            z_stack = image_queue.get()
            z_plane_queue.put(misc.find_most_in_focus_plane(z_stack))
            image_queue = Queue()

def command_thread(client, command_event, command_queue):
    while True:
        command_event.wait()

        print("Sending command to nuc")
        if command_queue == 24592:
            tcpip_nuc.is_stage_stopped(client, command_queue)
            command_queue = Queue()

        command_event.clear()