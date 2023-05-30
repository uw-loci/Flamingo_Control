# Includes functions to read and write text files specifically designed for the Flamingo
# create an option to read in a previous workflow but make minor changes

def calculate_zplanes(wf_dict, z_search_depth, framerate, plane_spacing):
    wf_dict['Stack Settings']['Number of planes'] = round(1000*float(z_search_depth)/plane_spacing)
    wf_dict['Stack Settings']['Change in Z axis (mm)'] = z_search_depth
    wf_dict['Experiment Settings'] ['Plane spacing (um)'] = plane_spacing #widest plane spacing allowed.
    wf_dict['Stack Settings']['Z stage velocity (mm/s)']  = str(plane_spacing*framerate/1000) #10um spacing and conversion to mm/s
    return wf_dict

def laser_or_LED(workflow_dict, laser_channel, laser_setting, LED_off='0.00 0', LED_on='50.0 1', laser_on = True):
    workflow_dict['Illumination Source'][laser_channel] = str(laser_setting)+' '+str(int(laser_on)) # 1 indicates that the laser should be used/on.
    if(laser_on):
        workflow_dict['Illumination Source']['LED_RGB_Board'] = LED_off
    else:
        workflow_dict['Illumination Source']['LED_RGB_Board'] = LED_on
    return workflow_dict

def dict_positions(workflow_dict, xyzr, zEnd, save_with_data=False, get_zstack = False):
    '''
    Take in a workflow dictionary and modify it to take a zstack from xyzr to zEnd (only the Z axis changes)
    By default the MIP will be returned, and the data saved to the drive.
    workflow_dict: A mapping between elements of a text file used to control the microscope, created in text_file_parsing.py
    xyzr = 4 element list of floats [x,y,z,r] 
    save_with_data: boolean to determine whether the snapshot is saved. When false, it would only be (depending on visualize_event flag) displayed in the GUI
    get_zstack: boolean to determine whether every frame of the zstack is returned to this program for processing
    WARNING: get_zstack WILL BE VERY SLOW AND LIKELY FAIL OVER SLOWER NETWORK CONNECTIONS LIKE WIFI
    '''
    x,y,z,r = xyzr
    if save_with_data:
        workflow_dict['Experiment Settings']['Comments'] = f'Snapshot at {x}, {y}, {z}, {r}'
        workflow_dict['Experiment Settings']['Save image data'] = 'Tiff'
    else:
        workflow_dict['Experiment Settings']['Save image data'] = 'NotSaved'

    workflow_dict['Start Position']['X (mm)'] = float(x)
    workflow_dict['Start Position']['Y (mm)'] = float(y)
    workflow_dict['Start Position']['Z (mm)'] = float(z)
    workflow_dict['End Position']['Z (mm)'] = float(zEnd)
    workflow_dict['End Position']['X (mm)'] = float(x)
    workflow_dict['End Position']['Y (mm)'] = float(y)
    workflow_dict['Start Position']['Angle (degrees)'] = float(r)
    workflow_dict['End Position']['Angle (degrees)'] = float(r)
    workflow_dict['Stack Settings']['Change in Z axis (mm)'] = abs(float(z)-float(zEnd))
    if get_zstack:
        workflow_dict['Experiment Settings']['Display max projection'] = "false"
        workflow_dict['Experiment Settings']['Work flow live view enabled'] = "true"
    else:
        workflow_dict['Experiment Settings']['Display max projection'] = "true"
        workflow_dict['Experiment Settings']['Work flow live view enabled'] = "false"

    return workflow_dict

def dict_comment(workflow_dict, comment='No Comment'):
    '''
    Take a workflow dict and a String to replace the comment and then return the updated dict.
    '''
    workflow_dict['Experiment Settings']['Comments'] = comment
    return workflow_dict

def dict_to_snap(workflow_dict, xyzr, framerate, plane_spacing, save_with_data=False):
    '''
    Take in a workflow dictionary and modify it to take a snapshot using the same imaging settings as the dict
    Snapshot will be acquired at the provided xyzr coordinates
    workflow_dict: A mapping between elements of a text file used to control the microscope, created in text_file_parsing.py
    xyzr = 4 element list of floats [x,y,z,r] 
    save_with_data: boolean to determine whether the snapshot is saved. When false, it would only be (depending on visualize_event flag) displayed in the GUI
    
    '''

    x,y,z,r = xyzr
    #Snap is mostly to show data to the user through the GUI and not intended as experimental results,
    #but the option is there to allow the user to save if desired.
    if save_with_data:
        workflow_dict['Experiment Settings']['Comments'] = f'Snapshot at {x}, {y}, {z}, {r}'
        workflow_dict['Experiment Settings']['Save image data'] = 'Tiff'
    else:
        workflow_dict['Experiment Settings']['Save image data'] = 'NotSaved'

    workflow_dict['Start Position']['X (mm)'] = float(x)
    workflow_dict['Start Position']['Y (mm)'] = float(y)
    workflow_dict['Start Position']['Z (mm)'] = float(z)
    workflow_dict['End Position']['Z (mm)'] = float(z)+0.01
    workflow_dict['End Position']['X (mm)'] = float(x)
    workflow_dict['End Position']['Y (mm)'] = float(y)
    workflow_dict['Start Position']['Angle (degrees)'] = float(r)
    workflow_dict['End Position']['Angle (degrees)'] = float(r)
    workflow_dict['Stack Settings']['Change in Z axis (mm)'] = 0.01
    workflow_dict['Experiment Settings']['Display max projection'] = "true"
    workflow_dict['Experiment Settings']['Work flow live view enabled'] = "false"
    workflow_dict['Stack Settings']['Number of planes'] = 1
    workflow_dict['Experiment Settings'] ['Plane spacing (um)'] = plane_spacing #widest plane spacing allowed.
    workflow_dict['Stack Settings']['Z stage velocity (mm/s)']  = str(plane_spacing*framerate/1000) #10um spacing and conversion to mm/s    

    return workflow_dict

    
def text_to_dict(filename):
    with open(filename, 'r') as f:
        # Create an empty dictionary to store the settings
        settings_dict = {}
        stack = []

        # Read the lines and parse them
        for line in f:
            # Strip leading and trailing whitespace from the line
            line = line.strip()

            if line.startswith('</') and line.endswith('>'):
                # End of a section
                closing_tag = line[2:-1]
                while stack:
                    current_dict = stack.pop()
                    if closing_tag == list(current_dict.keys())[0]:
                        break
            elif line.startswith('<') and line.endswith('>'):
                # Start of a new section
                section_name = line[1:-1]
                new_dict = {}
                if stack:
                    # Add the new dictionary to its parent dictionary
                    parent_dict = stack[-1]
                    parent_dict[section_name] = new_dict
                else:
                    # Top-level dictionary
                    settings_dict[section_name] = new_dict
                stack.append(new_dict)
            else:
                # Parse the key and value from the line
                key, value = line.split('=')
                current_dict = stack[-1]
                current_dict[key.strip()] = value.strip()

        return settings_dict
    
def dict_to_text(file_location, settings_dict):
    with open(file_location, 'w') as f:
        # Write the settings to the file
        write_dict(settings_dict, f, 0)


def write_dict(settings_dict, file, indent_level):
    indent = '  ' * indent_level
    for key, value in settings_dict.items():
        if isinstance(value, dict):
            # Start of a section
            file.write(f"{indent}<{key}>\n")
            write_dict(value, file, indent_level + 1)
            file.write(f"{indent}</{key}>\n")
        else:
            # Key-value pair
            file.write(f"{indent}{key} = {value}\n")

def dict_to_workflow(file_name, settings_dict):
    #print("dict_to_workflow "+file_name)
    # Start with the <Workflow Settings> tag
    output = '<Workflow Settings>\n'

    # Loop over each section in the settings dictionary
    for section, settings in settings_dict.items():
        # Add a header tag for the current section
        output += f'    <{section}>\n'
        # Loop over each key-value pair in the current section's settings dictionary
        for key, value in settings.items():
            # Add the key-value pair to the output string with proper indentation
            output += f'    {key} = {value}\n'
        # Add a closing tag for the current section
        output += f'    </{section}>\n'

    # Add the closing tag for the <Workflow Settings> tag
    output += '</Workflow Settings>'

    # Write the output string to the specified file
    with open(file_name, 'w', newline='\n') as f:
        f.write(output)

def workflow_to_dict(filename):
    #print('workflow to dict '+filename)
    with open(filename, 'r') as f:
        # Skip the first line
        next(f)

        # Create an empty dictionary to store the settings
        settings_dict = {'Experiment Settings': {},
                         'Camera Settings': {},
                         'Stack Settings': {},
                         'Start Position': {},
                         'End Position': {},
                         'Illumination Source': {},
                         'Illumination Path': {}
                         }

        # Read the rest of the lines and parse them
        current_section = ''
        for line in f:
            # Check if this line marks the start of a new section
            if '<' in line and '>' in line:
                current_section = line.strip()[1:-1]
            else:
                # Parse the key and value from the line
                key, value = line.strip().split('=')
                settings_dict[current_section][key.strip()] = value.strip()

        return settings_dict