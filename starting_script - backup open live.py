import tcpip_nuc
import socket
import os
import sys
FLAMINGO_IP = '10.129.37.17'
FLAMINGO_PORT = 53717
FLAMINGO_LISTEN = 53718
wf_file = "SingleImageWorkflow.txt"
wf_file = False
command = 4139
print(wf_file)
f = open(wf_file, "r")
print(f.read(50))
print(FLAMINGO_IP)
#source https://pythonprogramming.net/client-server-python-sockets/

# HOST = '' 
# PORT = 5555 
 
# s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
# try:
#     s.bind((HOST, PORT))
    
# except socket.error as msg:
#     print('Bind failed. Error Code : ' + str(msg[0]) + ' Message ' + msg[1])
#     sys.exit()
	
# print('Socket bind complete')
# s.listen(1)

# conn, addr = s.accept()

# print('Connected with ' + addr[0] + ':' + str(addr[1]))


tcpip_nuc.command_to_nuc(FLAMINGO_IP, FLAMINGO_PORT, wf_file, command)

# def threaded_client(conn):
#     conn.send(str.encode('Welcome, type your info\n'))

#     while True:
#         data = conn.recv(2048)
#         reply = 'Server output: '+ data.decode('utf-8')
#         if not data:
#             break
#         conn.sendall(str.encode(reply))
#     conn.close()

# while True:

#     conn, addr = s.accept()
#     print('connected to: '+addr[0]+':'+str(addr[1]))

#     start_new_thread(threaded_client,(conn,))