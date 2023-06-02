# Flamingo_Control (Py2Flamingo)

Only for use with [Flamingo systems](https://huiskenlab.com/resources/).

## Requirements
Currently only intended for use with Elsa. Could potentially be used with other T-spim systems with some editing of hard-coded values. The systems currently require version 2.16.2 of the Flamingo software for Py2Flamingo to interface correctly.
To connect to Elsa requires being at Morgridge on their network (not eduroam), or VPNed in.

## Current workflow:

### First steps
-------------------

Before using this software, be familiar with your own Flamingo microscope, and have a dataset on hand that you have collected on a sample similar to the one you want to image using Py2Flamingo. There will be several files that are produced along with that dataset you will need to set up the software.

Run __init__.py

The first step is a series of checks that need to be passed in order to connect and load up the GUI with default values. Requirements include:
1. A connection to the microscope - Elsa requires being on the Morgridge LAN, Wireless (not eduroam), or connected through a VPN. This step will vary from site to site.
2. Access to a FlamingoMetaData.txt file - these are generated when you run any workflow. It will go in the `microscope_settings` folder.
3. Access to a workflow text file, which will be copied to Zstack.txt within the `workflows` folder.

If any of these steps fail, the user will get a pop-up window informing them of which step has failed, and roughly what is needed. The software then connects on two ports, a `live imaging` port which collects the image data that would normally be displayed on the screen of the Linux box, and a `command` port which sends and recieves information about the microscope and what we want it to do. Four threads total are created at this point:
1. Command listening thread: get information from the microscope such as whether it is in the Idle State.
2. Command sending thread: Send commands, like move to XYZR position, get settings, etc.
3. Image data listening thread: Get either MIPs (fine for wireless usually) or Z-stacks (needs a hard-wired connection)
4. Processing thread: For performing image processing actions while other threads are occupied, without slowing down the acquisition.

Once all of this is done, the GUI is displayed for the user.

### The GUI
-----------

![Current status of GUI](https://github.com/uw-loci/Flamingo_Control/blob/main/images/GUI.png?raw=true)

The GUI is populated with some initial data based on the provided workflow file, inlcuding the laser (lasers?) used, laser power, information about the microscope connection, and a default search range along the Z axis. In most cases, the beige set of coordinates will be blank when the program is first run, as they indicate the tip of the sample holder and need to be added at least once. Once they are included and "Find Sample" is run, a file `[microscope]_start_position.txt` will be created in the `microscope_settings` folder.

The cyan coordinate fields are associated with the similarly colored buttons, and can be used to perform a few manual tasks, like moving the microscope to set coordinates, taking a snapshot with the current settings at the cyan location, or setting the Home position within the true GUI software on the Mac. 

Finally, there is an option to "Copy position of image" which takes the coordinates shown under the currently displayed image and copies them to the cyan coordinates.

### Find sample
------------

Find sample is a first simple test of the system, where:
1. A starting point is established based on the tip of the sample holder (or close)
2. Take multiple Z stacks, iterating down the Y axis, and stop when a peak in the fluorescence intensity is found (see calculations.py)
3. Move back to the peak in the Y axis MIPs, and scan through the `Z Search Depth (mm)` along Z, and similarly stop when a peak is detected.
4. Move to the peak found in the Z direction scan.
5. Optionally, at this point, use the "Copy position of image" and "Set Home on Mac" buttons to store the position of the sample as Home on the main GUI.




## Other information about the program
----------------------

 ![Overall control flow](https://github.com/uw-loci/Flamingo_Control/blob/main/images/workflow.PNG?raw=true)
-------------------

**Communicating with the Flamingo**  
 
Commands: Individual instructions, limited, e.g. move X axis position to "value"

Workflows: Also a command, but followed by a text file structured something like a JSON that conducts and entire workflow, e.g. a time series/Zstack with certain cameras, lasers, etc. and handles data storage/distribution.

Main file - __init__.py  

Starts the process by launching the GUI.py (aka Py2FlamingoGUI)
Some Queue and Event objects are created through global_objects.py at this point, which help handle data transfer and pacing between the threads.
Which calls FlamingoConnect.py to connect (microscope_connect.py) and collect useful information to populate the GUI. FlamingoConnect also starts four threads (threads.py) to handle communicating with the microscope (tcpip_nuc.py, originally courtesy of Gesine Fiona Müller from the Huisken lab) and data processing.

### **Files related to button commands are directly in the py2flamingo folder, currently.**
-------------------

The "functions" folder contains a few Python files that define useful functions, like connecting to the microscope or creating threads.

`threads.py` handle the threads mentioned above.

`tcpip_nuc.py` directly controls the information sent to the nuc

`calculations.py` has extra functions for processing data, like calculating the most in focus frame of a Z stack.

`text_file_parsing.py` creates and reads workflow and/or metadata files. Workflows are text files saved to the current computer/OS that can be sent to the "nuc" to perform an acquisition.
They are currently parsed and stored as dictionaries in order to edit particular entries, then re-saved as text files when, for example, you need to change Y position before taking another Z stack.

`microscope_connect.py` binds the sockets and starts the threads to handle communication with the microscope, and closing the connection when finished.

image_display.py currently only handles some image processing so that 16bit images show up nicely in the GUI.

Control of what runs when is handled by a fairly messy set of "events" to let different threads know when to check a "queue" for the presence of data to process or a command to send. 

### **Short term goals**
Streamline the workflow to minimize the number of events, while maintaining sufficient flexibility to grow out the code for additional functionality.

### **Important non-coding files**
`functions/command_list.txt` contains commands that can be sent to the controller, and their numerical codes. These can be used to send workflows or request data from the system, among other things.

workflows/???.txt workflows are all variations on the workflow file the Flamingo controller expects to conduct some sort of experiment, be it a Zstack, time series, tile, or combination of those.

`microscope_settings/`

`FlamingoMetaData.txt` contains important information about your instrument and allows Python to connect correctly.  

`ScopeSettings.txt` contains useful information about the system configuration that can help determine pixel size metadata etc.
???_start position.txt should contain the start position to search for the sample. Generally this should be the tip of the sample holder, though you may want larger Z value to make the search quicker (ignore space near the tip of sample holder).  

`[microscope]_start_position.txt` contains the starting position to search for the sample for the given microscope.


In progress: environment.yml and setup.py file creation. Poetry?