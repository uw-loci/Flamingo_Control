# Flamingo_Control
Currently only intended for use with Elsa. Could potentially be used with other T-spim systems with some editing of hard-coded values.
To connect to Elsa requires being at Morgridge on their network (not eduroam), or VPNed in.
Current workflow:
Run GUI.py
There are two checks that need to be passed first - a Zstack.txt file needs to be in the workflows folder, and a FlamingoMetaData.txt needs to be in the microscope_settings folder.
If these are not present, the user will be prompted to get find a copy to use as a baseline for connecting to the microscope.
Once the files are acquired, a GUI will let the user double check the settings and make any changes.
They can then Find Sample to locate the sample, which will proceed from a pre-recorded start point down the Y axis until it finds a "bump" in the fluorescence intensity.
A visual of each Zstack (a maximum intensity projection) collected as the program pans down the Y axis of the sample tube will be shown to the user.
Once a maxima is found in the IF intensity of the channel selected through the GUI, another Z stack will be taken at that Y axis location to find a rough depth for the sample.
Pictures of the various MIPs will be stored in the output_png folder, along with an initial brightfield image of the starting point, and a final image of the sample where the instrument found focus.

![Overall control flow](https://github.com/uw-loci/Flamingo_Control/blob/main/images/workflow.PNG?raw=true)

 Controlling a Flamingo microscope from Python
 Two main methods to send information
 Commands: Individual instructions, limited, e.g. move X axis position to "value"
 Workflows: A command, but followed by a text file structured something like a JSON that conducts and entire workflow, e.g. a time series/Zstack with certain cameras, lasers, etc. and handles data storage/distribution.
 

Main file - GUI.py

![Current status of GUI](https://github.com/uw-loci/Flamingo_Control/blob/main/images/GUI.png?raw=true)

Find focus, go to position, and take IF snapshot are all funcitoning now. There is room to add new functionality. The graphical representation to the right is somewhat laggy, and may skip images.

The thread then connects to the microscope and spins off four more threads to handle:

1. Sending commands - send commands and workflows
2. Receiving image data - parse the bytestreams into individual images and/or create a stack. Add the data to an image_queue to be used by other threads.
3. Receiving command responses - how we obtain data that is not an image from the microscope, like the pixel size.
4. Processing data - an additional thread to handle image processing tasks outside of the other threads.

The "functions" folder contains a few Python files that define useful functions, like connecting to the microscope or creating threads.

threads.py handle the threads mentioned above.

tcpip_nuc.py directly controls the information sent to the nuc

calculations.py has extra functions for processing data, like calculating the most in focus frame of a Z stack.

text_file_parsing.py creates and reads workflow and/or metadata files. Workflows are text files saved to the current computer/OS that can be sent to the "nuc" to perform an acquisition.
They are currently parsed and stored as dictionaries in order to edit particular entries, then re-saved as text files when, for example, you need to change Y position before taking another Z stack.

microscop_connect.py binds the sockets and starts the threads to handle communication with the microscope, and closing the connection when finished.

image_display.py currently only handles some image processing so that 16bit images show up nicely in the GUI.



Control of what runs when is handled by a fairly messy set of "events" to let different threads know when to check a "queue" for the presence of data to process or a command to send. 

Short term goals -
Streamline the workflow to minimize the number of events, while maintaining sufficient flexibility to grow out the code for additional functionality.

Important non-coding files
functions/command_list.txt contains commands that can be sent to the controller, and their numerical codes. These can be used to send workflows or request data from the system, among other things.
workflows/???.txt workflows are all variations on the workflow file the Flamingo controller expects to conduct some sort of experiment, be it a Zstack, time series, tile, or combination of those.
microscope_settings/
FlamingoMetaData.txt contains important information about your instrument and allows Python to connect correctly.
ScopeSettings.txt contains useful information about the system configuration that can help determine pixel size metadata etc.
???_start position.txt should contain the start position to search for the sample. Generally this would be the tip of the sample holder, though you may want larger Z value to make the search quicker (ignore space near the tip of sample holder).


Use the environment.yml file to set up the Conda environment.