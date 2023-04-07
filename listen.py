import socket
import struct
import threading
from PIL import Image, ImageOps

IP_REMOTE = '10.129.37.17' # Change this to the remote IP address
PORT_REMOTE = 53718 # Change this to the remote port number
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

# Connect to the remote server
sock.connect((IP_REMOTE, PORT_REMOTE))
print(sock)

while True:
    try:
        # receive the header
        header_data = sock.recv(40)
        if len(header_data) != 40:
            raise ValueError(f'Header length should be 40 bytes, not {len(header_data)}')
        
        # parse the header
        print("header received")
        header = struct.unpack('I I I I I I I I I I', header_data)
        image_size, image_width, image_height, start_index, stop_index = header[0], header[1], header[2], header[8], header[9]
        stack_size = stop_index - start_index
        
        # receive the image data
        image_data = b''
        while len(image_data) < image_size:
            data = sock.recv(image_size - len(image_data))
            if not data:
                raise socket.error('Incomplete image data')
            image_data += data
        
        # convert and save the image
        image = Image.frombytes('I;16', (image_width, image_height), image_data)
        rotated_image = image.rotate(90, expand=True)
        rotated_image.save(f'output.png')
        grayscale_image = rotated_image.convert("L")
        grayscale_image.show()
        # return the grayscale image
    except socket.error as e:
        print(f'Socket error: {e}')
        sock.close()
        break
        

#this might be specific to images
# def listen_for_snap(IP_REMOTE, PORT_REMOTE, index, THREAD_GO):
#     # Create a socket object
#     sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

#     # Connect to the remote server
#     sock.connect((IP_REMOTE, PORT_REMOTE))
#     print(sock)

#     while THREAD_GO:
#         try:
#             # receive the header
#             header_data = sock.recv(40)
#             if len(header_data) != 40:
#                 raise ValueError(f'Header length should be 40 bytes, not {len(header_data)}')
            
#             # parse the header
#             print("header received")
#             header = struct.unpack('I I I I I I I I I I', header_data)
#             image_size, image_width, image_height, start_index, stop_index = header[0], header[1], header[2], header[8], header[9]
#             stack_size = stop_index - start_index
            
#             # receive the image data
#             image_data = b''
#             while len(image_data) < image_size:
#                 data = sock.recv(image_size - len(image_data))
#                 if not data:
#                     raise socket.error('Incomplete image data')
#                 image_data += data
            
#             # convert and save the image
#             image = Image.frombytes('I;16', (image_width, image_height), image_data)
#             rotated_image = image.rotate(90, expand=True)
#             rotated_image.save(f'output{index}.png')
#             grayscale_image = rotated_image.convert("L")
#             #grayscale_image.show()
#             # return the grayscale image
#             return grayscale_image
            
#         except socket.error as e:
#             print(f'Socket error: {e}')
#             sock.close()
#             break
            
#         except Exception as e:
#             print(f'Error: {e}')
#             continue

#     return None

    
    
####
#incomplete - HANDLE RAW DATA
#####
# def listen_for_stack(IP_REMOTE, PORT_REMOTE, index, THREAD_GO):
#     # Create a socket object
#     sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

#     # Connect to the remote server
#     sock.connect((IP_REMOTE, PORT_REMOTE))
#     print(sock)

#     while True:
#         try:
#             # receive the header
#             header_data = sock.recv(40)
#             if len(header_data) != 40:
#                 raise ValueError(f'Header length should be 40 bytes, not {len(header_data)}')
            
#             # parse the header
#             print("header received")
#             header = struct.unpack('I I I I I I I I I I', header_data)
#             image_size, image_width, image_height, start_index, stop_index = header[0], header[1], header[2], header[8], header[9]
#             stack_size = stop_index - start_index
            
#             # receive the image data
#             image_data = b''
#             while len(image_data) < image_size:
#                 data = sock.recv(image_size - len(image_data))
#                 if not data:
#                     raise socket.error('Incomplete image data')
#                 image_data += data
            
#             # convert and save the image
#             image = Image.frombytes('I;16', (image_width, image_height), image_data)
#             rotated_image = image.rotate(90, expand=True)
#             rotated_image.save(f'output{index}.png')
#             grayscale_image = rotated_image.convert("L")

#             # return the grayscale image
#             return grayscale_image
            
#         except socket.error as e:
#             print(f'Socket error: {e}')
#             sock.close()
#             break
            
#         except Exception as e:
#             print(f'Error: {e}')
#             continue

#     return None