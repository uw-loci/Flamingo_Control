# Flamingo_Control (Py2Flamingo)

This repository provides control software for Flamingo systems. It is designed for use with [Flamingo systems](https://huiskenlab.com/resources/).

## Requirements
- Currently intended for use with Zion.
- Requires version 2.16.2 of the Flamingo software for Py2Flamingo to interface correctly.
- To connect to Zion, you need to be on Morgridge's network or VPNed in.
- For any new system, certain text files will be necessary to target the system (First Steps 2). Warning windows will pop up if you do not have these in the correct places.

## Getting Started

### First Steps
Before using this software, ensure that you are familiar with your Flamingo microscope and have a dataset on hand. Follow these steps:

1. Run `__init__.py` to pass a series of checks and connect to the microscope.
2. Make sure you have access to the necessary files:
   - FlamingoMetaData.txt (generated during any workflow) should be placed in the `microscope_settings` folder and contains the IP address and port information.
   - Copy a workflow text file to Zstack.txt within the `workflows` folder. This file is used as the basis for many of the default settings - if you want to change your default settings, collect a new workflow file and replace it.

If any of these steps fail, a pop-up window will inform you of the issue and the required actions. Once the software establishes a connection and creates the necessary threads, the GUI will be displayed.

### The GUI
![Current status of GUI](https://github.com/uw-loci/Flamingo_Control/blob/main/images/GUI.png?raw=true)

The GUI is populated with initial data from the workflow file. It includes information about the laser, microscope connection, and default search range along the Z axis. The beige set of coordinates will be blank initially and needs to be added manually. The cyan coordinate fields are associated with buttons for performing manual tasks.

### Find Sample
The "Find Sample" feature is a simple test of the system that performs the following steps:
1. Establish a starting point based on the tip of the sample holder. You will need to do this manually the first time.
2. Take multiple Z stacks while iterating down the Y axis, stopping at the first peak in the fluorescence intensity.
3. Scan through a range determined by `Z Search Depth (mm)` to find the peak in the Z direction.
4. Move to the peak found in the Z direction.
Warning, this may fail if your sample is very bent, such that it is entirely out of frame in the X dimension. It is not intended to search the entire possible volume!

### Other funcitonality
Other functions are available, such as copying the position of the image and setting the Home position on the main GUI. The current set of buttons are a simple starting point, with the goal of making it possible for the user to add their own funcitonality through editing the Python files.

## Program Structure

### Communicating with the Flamingo
Commands and workflows are used to interact with the Flamingo system. The main files and their functionalities are as follows:

- `__init__.py`: Launches the GUI and sets up the necessary objects.
- `FlamingoConnect.py`: Connects to the microscope and starts the required threads for communication and data processing.
- `threads.py`: Handles the various threads, such as command listening, command sending, image data listening, and processing.
- `tcpip_nuc.py`: Controls the information sent to the microscope.
- `calculations.py`: Provides additional functions for data processing.
- `text_file_parsing.py`: Handles the creation and reading of workflow and metadata files.
- `microscope_connect.py`: Binds the sockets and manages communication with the microscope.
- `image_display.py`: Handles image processing for proper display in the GUI.

### Important Files and Folders
- `functions/command_list.txt`: Contains commands and their numerical codes for interacting with the controller.
- `workflows/???.txt`: Workflow files for conducting experiments.
- `microscope_settings/`: Folder containing important configuration files.
   - `FlamingoMetaData.txt`: Contains instrument information for correct Python connection.
   - `ScopeSettings.txt`: Contains system configuration details.
   - `???_start_position.txt`: Contains the starting position for searching the sample.
Some images showing the folder structure and files:
![Current status of GUI](https://github.com/uw-loci/Flamingo_Control/blob/main/images/Folder_structure.PNG?raw=true | height=100)
![Current status of GUI](https://github.com/uw-loci/Flamingo_Control/blob/main/images/microscope_settings_folder.PNG?raw=true | height=100)
![Current status of GUI](https://github.com/uw-loci/Flamingo_Control/blob/main/images/Output_png_folder.PNG?raw=true | height=100)
### Short-term Goals
The short-term goals for this project include streamlining the workflow and improving flexibility.

In progress: environment.yml and setup.py file creation. Poetry? Create a generic button. It would be great to create a set of prompts that could be fed into ChatGPT that would allow it to create new buttons with new functionality.