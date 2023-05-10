# Flamingo_Control
 Controlling a Flamingo microscope from Python
 Two main methods to send information
 Commands: Individual instructions, limited, e.g. move X axis position to "value"
 Workflows: A command, but followed by a text file structured something like a JSON that conducts and entire workflow, e.g. a time series/Zstack with certain cameras, lasers, etc. and handles data storage/distribution.
 

Main file - cc_loop.py

The command control loop creates the sockets/ports and connections, and spins out threads.

threads.py contains several threads to handle:
1. Sending commands
2. Receiving data
3. Receiving command responses
4. Processing data

tcpip_nuc.py directly controls the information sent to the nuc

misc.py has extra functions for processing data, like calculating the most in focus frame of a Z stack.

workflow_gen.py creates and reads workflow files. Workflows are text files saved to the current computer/OS that can be sent to the "nuc" to perform an acquisition.
They are currently parsed and stored as dictionaries in order to edit particular entries, then re-saved as text files when, for example, you need to change Y position before taking another Z stack.