# Flamingo_Control
Current workflow:
Run GUI.py
It will check to see if you have a ZStack.txt file in the workflows folder, if not, it will ask the user to find a workflow file it can use as a template.
Once the workflow file is acquired, a GUI will let the user double check the settings and make any changes.
They can then Find Sample to locate the sample, which will proceed from a pre-recorded start point down the Y axis until it finds a "bump" in the fluorescence intensity.
Pictures of the various MIPs will be stored in the output_png folder, along with an initial brightfield image of the starting point, and a final image of the sample.



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


Control of what runs when is handled by a fairly messy set of "events" to let different threads know when to check a "queue" for the presence of data to process or a command to send. 

Short term goals -
Improved commenting and making sure all of the lightsheet interactions are working as expected.
Improve data transfer rate by binning at the camera
Streamline the workflow to minimize the number of events, while maintaining sufficient flexibility to grow out the code for additional functionality.


Use the environment.yml file to set up the Conda environment.