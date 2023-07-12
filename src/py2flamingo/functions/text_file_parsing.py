#TODO complete check_coordinate_limits to validate that ranges in workflow are within soft limits

# Includes functions to read and write text files specifically designed for the Flamingo
# create an option to read in a previous workflow but make minor changes
from typing import Sequence
import os
import re
def calculate_zplanes(wf_dict, z_search_depth, framerate, plane_spacing):
    """
    Calculates and updates the number of z-planes and related settings in a Flamingo workflow dictionary.

    This function takes a Flamingo workflow dictionary, the desired z-search depth, framerate, and plane spacing,
    and calculates and updates the necessary settings for z-planes in the workflow dictionary.

    Parameters
    ----------
    wf_dict : dict
        Flamingo workflow dictionary.
    z_search_depth : float
        Desired z-search depth in millimeters.
    framerate : float
        Framerate in frames per second.
    plane_spacing : float
        Spacing between adjacent z-planes in micrometers.

    Returns
    -------
    dict
        Updated Flamingo workflow dictionary.
    """
    # Calculate the number of planes based on z-search depth and plane spacing
    wf_dict["Stack Settings"]["Number of planes"] = round(
        1000 * float(z_search_depth) / plane_spacing
    )

    # Update related settings
    wf_dict["Stack Settings"]["Change in Z axis (mm)"] = z_search_depth
    wf_dict["Experiment Settings"]["Plane spacing (um)"] = plane_spacing
    wf_dict["Stack Settings"]["Z stage velocity (mm/s)"] = str(
        plane_spacing * framerate / 1000
    )

    return wf_dict


def laser_or_LED(
    workflow_dict,
    laser_channel,
    laser_setting,
    LED_off="0.00 0",
    LED_on="50.0 1",
    laser_on=True,
):
    """
    Updates the illumination source settings in a Flamingo workflow dictionary to use a laser or LED.

    This function takes a Flamingo workflow dictionary, a laser channel, laser setting, and optional LED settings,
    and updates the illumination source settings in the workflow dictionary to use the specified laser or LED.

    Parameters
    ----------
    workflow_dict : dict
        Flamingo workflow dictionary.
    laser_channel : str
        Laser channel to update in the workflow dictionary.
    laser_setting : float
        Laser setting to use.
    LED_off : str, optional
        LED off setting. Default is "0.00 0".
    LED_on : str, optional
        LED on setting. Default is "50.0 1".
    laser_on : bool, optional
        Flag indicating whether the laser should be turned on. Default is True.

    Returns
    -------
    dict
        Updated Flamingo workflow dictionary.
    """
    # Update the laser channel with the specified laser setting and laser_on flag
    workflow_dict["Illumination Source"][laser_channel] = (
        str(laser_setting) + " " + str(int(laser_on))
    )

    # Update the LED setting based on the laser_on flag
    if laser_on:
        workflow_dict["Illumination Source"]["LED_RGB_Board"] = LED_off
    else:
        workflow_dict["Illumination Source"]["LED_RGB_Board"] = LED_on

    return workflow_dict

def dict_to_bounds(bounding_dict):
    """
    Convert a dict to two sets of coordinates defining a bounding cube
    """
    points = []
    for point in bounding_dict.values():
        coordinates = []
        for coord in point.values():
            coordinates.append(float(coord))
        points.append(coordinates)

    return points


def check_coordinate_limits(workflow_dict):
    """
    Validate the coordinates in a workflow dictionary against predefined limits.

    This function checks whether the 'start' and 'end' coordinates for the X, Y, and Z axes
    in the provided workflow dictionary are strictly within the limits specified in a settings file.
    The settings file is assumed to be located at 'microscope_settings/ScopeSettings.txt' and is
    expected to be in a format that can be converted to a dictionary using the `text_to_dict` function.

    Parameters:
    workflow_dict (dict): A dictionary containing 'start' and 'end' coordinates for the X, Y, and Z axes.

    Returns:
    bool: True if all coordinates are strictly within the limits, False otherwise.

    Raises:
    ValueError: If a 'start' or 'end' coordinate is outside the limits.
    """
    # Load the scope settings from the settings file
    scope_settings = text_to_dict(os.path.join('microscope_settings', 'ScopeSettings.txt'))

    # Loop over each axis
    for axis in ['x', 'y', 'z']:
        # Extract the coordinate limits from the settings
        min_limit = float(scope_settings['Stage limits'][f'Soft limit min {axis}-axis'])
        max_limit = float(scope_settings['Stage limits'][f'Soft limit max {axis}-axis'])

        # Extract the 'start' and 'end' coordinates from the workflow dictionary
        workflow_start = float(workflow_dict['Start Position'][f'{axis.upper()} (mm)'])
        workflow_end = float(workflow_dict['End Position'][f'{axis.upper()} (mm)'])

        # Check whether the 'start' and 'end' coordinates are strictly within the limits
        if not (min_limit < workflow_start < max_limit and min_limit < workflow_end < max_limit):
            raise ValueError(f"The {axis}-axis workflow coordinates are outside the limits. "
                             f"Start: {workflow_start}, End: {workflow_end}, "
                             f"Min Limit: {min_limit}, Max Limit: {max_limit}")

    # If all coordinates are within the limits, return True
    return True


# def dict_positions(
#     workflow_dict: map,
#     xyzr: Sequence[float],
#     zEnd: float,
#     save_with_data=False,
#     get_zstack=False,
# ):
#     """
#     Take in a workflow dictionary and modify it to take a zstack from xyzr to zEnd (only the Z axis changes)
#     By default the MIP will be returned, and the data saved to the drive.
#     workflow_dict: A mapping between elements of a text file used to control the microscope, created in text_file_parsing.py
#     xyzr = 4 element list of floats [x,y,z,r]
#     save_with_data: boolean to determine whether the snapshot is saved. When false, it would only be (depending on visualize_event flag) displayed in the GUI
#     get_zstack: boolean to determine whether every frame of the zstack is returned to this program for processing
#     WARNING: get_zstack WILL BE VERY SLOW AND LIKELY FAIL OVER SLOWER NETWORK CONNECTIONS LIKE WIFI
#     """
#     x, y, z, r = xyzr
#     if save_with_data:
#         workflow_dict["Experiment Settings"][
#             "Comments"
#         ] = f"Snapshot at {x}, {y}, {z}, {r}"
#         workflow_dict["Experiment Settings"]["Save image data"] = "Tiff"
#     else:
#         workflow_dict["Experiment Settings"]["Save image data"] = "NotSaved"

#     workflow_dict["Start Position"]["X (mm)"] = float(x)
#     workflow_dict["Start Position"]["Y (mm)"] = float(y)
#     workflow_dict["Start Position"]["Z (mm)"] = float(z)
#     workflow_dict["End Position"]["Z (mm)"] = float(zEnd)
#     workflow_dict["End Position"]["X (mm)"] = float(x)
#     workflow_dict["End Position"]["Y (mm)"] = float(y)
#     workflow_dict["Start Position"]["Angle (degrees)"] = float(r)
#     workflow_dict["End Position"]["Angle (degrees)"] = float(r)
#     workflow_dict["Stack Settings"]["Change in Z axis (mm)"] = abs(
#         float(z) - float(zEnd)
#     )
#     if get_zstack:
#         workflow_dict["Experiment Settings"]["Display max projection"] = "false"
#         workflow_dict["Experiment Settings"]["Work flow live view enabled"] = "true"
#     else:
#         workflow_dict["Experiment Settings"]["Display max projection"] = "true"
#         workflow_dict["Experiment Settings"]["Work flow live view enabled"] = "false"

#     return workflow_dict


def dict_positions(
    workflow_dict: dict,
    xyzr: Sequence[float],
    xyzr2: Sequence[float] = None,
    zEnd: float = None,
    save_with_data=False,
    get_zstack=False,
):
    """
    Take in a workflow dictionary and modify it to take a zstack from xyzr to zEnd (only the Z axis changes)
    By default, the MIP will be returned, and the data saved to the drive.
    workflow_dict: A mapping between elements of a text file used to control the microscope, created in text_file_parsing.py
    xyzr = 4 element list of floats [x, y, z, r]
    xyzr2 (optional): 4 element list of floats for the second set of position values [x2, y2, z2, r2]
    zEnd (optional): float representing the end position on the Z axis when xyzr2 is not provided
    save_with_data: boolean to determine whether the snapshot is saved. When false, it would only be (depending on visualize_event flag) displayed in the GUI
    get_zstack: boolean to determine whether every frame of the zstack is returned to this program for processing
    WARNING: get_zstack WILL BE VERY SLOW AND LIKELY FAIL OVER SLOWER NETWORK CONNECTIONS LIKE WIFI
    """
    x, y, z, r = xyzr
    workflow_dict["Start Position"]["X (mm)"] = float(x)
    workflow_dict["Start Position"]["Y (mm)"] = float(y)
    workflow_dict["Start Position"]["Z (mm)"] = float(z)
    workflow_dict["Start Position"]["Angle (degrees)"] = float(r)

    if xyzr2 is not None:
        x2, y2, z2, r2 = xyzr2
        workflow_dict["End Position"]["X (mm)"] = float(x2)
        workflow_dict["End Position"]["Y (mm)"] = float(y2)
        workflow_dict["End Position"]["Z (mm)"] = float(z2)
        workflow_dict["End Position"]["Angle (degrees)"] = float(r2)
        workflow_dict["Stack Settings"]["Change in Z axis (mm)"] = abs(float(z) - float(z2))
    elif zEnd is not None:
        workflow_dict["End Position"]["X (mm)"] = float(x)
        workflow_dict["End Position"]["Y (mm)"] = float(y)
        workflow_dict["End Position"]["Z (mm)"] = float(zEnd)
        workflow_dict["End Position"]["Angle (degrees)"] = float(r)
        workflow_dict["Stack Settings"]["Change in Z axis (mm)"] = abs(float(z) - float(zEnd))
    else:
        raise ValueError("Either xyzr2 or zEnd must be provided.")

    if save_with_data:
        workflow_dict["Experiment Settings"]["Comments"] = f"Snapshot at {x}, {y}, {z}, {r}"
        workflow_dict["Experiment Settings"]["Save image data"] = "Tiff"
    else:
        workflow_dict["Experiment Settings"]["Save image data"] = "NotSaved"

    if get_zstack:
        workflow_dict["Experiment Settings"]["Display max projection"] = "false"
        workflow_dict["Experiment Settings"]["Work flow live view enabled"] = "true"
    else:
        workflow_dict["Experiment Settings"]["Display max projection"] = "true"
        workflow_dict["Experiment Settings"]["Work flow live view enabled"] = "false"

    return workflow_dict


def dict_comment(workflow_dict: map, comment="No Comment"):
    """
    Take a workflow dict and a String to replace the comment and then return the updated dict.
    """
    workflow_dict["Experiment Settings"]["Comments"] = comment
    return workflow_dict


def dict_save_directory(workflow_dict: map, directory="Default"):
    workflow_dict["Experiment Settings"]["Save image directory"] = directory
    return workflow_dict


def dict_to_snap(
    workflow_dict: map,
    xyzr: Sequence[float],
    framerate: float,
    plane_spacing: float,
    save_with_data=False,
):
    """
    Take in a workflow dictionary and modify it to take a snapshot using the same imaging settings as the dict
    Snapshot will be acquired at the provided xyzr coordinates
    workflow_dict: A mapping between elements of a text file used to control the microscope, created in text_file_parsing.py
    xyzr = 4 element list of floats [x,y,z,r]
    save_with_data: boolean to determine whether the snapshot is saved. When false, it would only be (depending on visualize_event flag) displayed in the GUI

    """

    x, y, z, r = xyzr
    # Snap is mostly to show data to the user through the GUI and not intended as experimental results,
    # but the option is there to allow the user to save if desired.
    if save_with_data:
        workflow_dict["Experiment Settings"][
            "Comments"
        ] = f"Snapshot at {x}, {y}, {z}, {r}"
        workflow_dict["Experiment Settings"]["Save image data"] = "Tiff"
    else:
        workflow_dict["Experiment Settings"]["Save image data"] = "NotSaved"

    workflow_dict["Start Position"]["X (mm)"] = float(x)
    workflow_dict["Start Position"]["Y (mm)"] = float(y)
    workflow_dict["Start Position"]["Z (mm)"] = float(z)
    workflow_dict["End Position"]["Z (mm)"] = float(z) + 0.01
    workflow_dict["End Position"]["X (mm)"] = float(x)
    workflow_dict["End Position"]["Y (mm)"] = float(y)
    workflow_dict["Start Position"]["Angle (degrees)"] = float(r)
    workflow_dict["End Position"]["Angle (degrees)"] = float(r)
    workflow_dict["Stack Settings"]["Change in Z axis (mm)"] = 0.01
    workflow_dict["Experiment Settings"]["Display max projection"] = "true"
    workflow_dict["Experiment Settings"]["Work flow live view enabled"] = "false"
    workflow_dict["Stack Settings"]["Number of planes"] = 1
    workflow_dict["Experiment Settings"][
        "Plane spacing (um)"
    ] = plane_spacing  # widest plane spacing allowed.
    workflow_dict["Stack Settings"]["Z stage velocity (mm/s)"] = str(
        plane_spacing * framerate / 1000
    )  # 10um spacing and conversion to mm/s

    return workflow_dict

def is_valid_filename(filename):


    # Check for invalid characters
    if re.search(r'[<>:"/\\|?*]', filename):
        return False

    # Check for reserved words
    if filename.upper() in ['CON', 'PRN', 'AUX', 'NUL', 'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9', 'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9']:
        return False

    return True


def text_to_dict(filename: str) -> dict:
    """
    Converts a text file containing Flamingo settings into a dictionary.

    This function reads a text file with Flamingo settings and converts it into a dictionary format.
    The text file should follow a specific format where each line represents a key-value pair in the format "key = value".
    Sections and nested sections are represented using XML-like tags "<section>...</section>".

    Parameters
    ----------
    filename : str
        The path to the text file containing the Flamingo settings.

    Returns
    -------
    dict
        A dictionary representation of the Flamingo settings.
    """
    with open(filename, "r") as f:
        settings_dict = {}
        stack = []

        for line in f:
            line = line.strip()

            if line.startswith("</") and line.endswith(">"):
                closing_tag = line[2:-1]
                while stack:
                    current_dict = stack.pop()
                    if closing_tag == list(current_dict.keys())[0]:
                        break
            elif line.startswith("<") and line.endswith(">"):
                section_name = line[1:-1]
                new_dict = {}
                if stack:
                    parent_dict = stack[-1]
                    parent_dict[section_name] = new_dict
                else:
                    settings_dict[section_name] = new_dict
                stack.append(new_dict)
            else:
                key, value = line.split("=")
                current_dict = stack[-1]
                current_dict[key.strip()] = value.strip()

    return settings_dict


def dict_to_text(file_location: str, settings_dict: dict):
    """
    Writes Flamingo settings from a dictionary to a text file.

    This function writes Flamingo settings stored in a dictionary to a text file.
    The settings are written in the format "key = value", and sections and nested sections are represented
    using XML-like tags "<section>...</section>".

    Parameters
    ----------
    file_location : str
        The path to the output text file.
    settings_dict : dict
        A dictionary containing the Flamingo settings to write.
    """
    with open(file_location, "w", newline="\n") as f:
        write_dict(f, settings_dict, 0)


def write_dict(file: str, settings_dict: map, indent_level: int):
    """
    Recursively writes dictionary data to a file with the specified indentation level.

    This function is used to recursively write a dictionary and its nested sections to a file.
    Each section is represented by XML-like tags ("<section>...</section>") and key-value pairs are written as "key = value".

    Parameters
    ----------
    file : str
        The file object or file path to write the dictionary data.
    settings_dict : dict
        The dictionary data to write to the file.
    indent_level : int
        The current indentation level for nested sections.

    Returns
    -------
    None
    """
    indent = "  " * indent_level
    for key, value in settings_dict.items():
        if isinstance(value, dict):
            # Start of a section
            file.write(f"{indent}<{key}>\n")
            write_dict(file, value, indent_level + 1)
            file.write(f"{indent}</{key}>\n")
        else:
            # Key-value pair
            file.write(f"{indent}{key} = {value}\n")


def dict_to_workflow(file_name: str, settings_dict: dict):
    """
    Converts a dictionary of Flamingo settings to a Flamingo workflow file.

    This function takes a dictionary of Flamingo settings and converts it into a Flamingo workflow file format.
    The settings are written in the format "key = value", with XML-like tags representing sections and nested sections.

    Parameters
    ----------
    file_name : str
        The path to the output Flamingo workflow file.
    settings_dict : dict
        A dictionary containing the Flamingo settings to convert.

    Returns
    -------
    None
        This function does not return anything, but writes the Flamingo workflow file to the specified path.
    """
    # Start with the <Workflow Settings> tag
    output = "<Workflow Settings>\n"

    # Loop over each section in the settings dictionary
    for section, settings in settings_dict.items():
        # Add a header tag for the current section
        output += f"    <{section}>\n"
        # Loop over each key-value pair in the current section's settings dictionary
        for key, value in settings.items():
            # Add the key-value pair to the output string with proper indentation
            output += f"    {key} = {value}\n"
        # Add a closing tag for the current section
        output += f"    </{section}>\n"

    # Add the closing tag for the <Workflow Settings> tag
    output += "</Workflow Settings>"

    # Write the output string to the specified file
    with open(file_name, "w", newline="\n") as f:
        f.write(output)


def workflow_to_dict(filename: str) -> dict:
    """
    Converts a Flamingo workflow file into a dictionary.

    This function reads a Flamingo workflow file and converts it into a dictionary format.
    The workflow file should follow a specific format where each line represents a key-value pair in the format "key = value".
    The file should also contain XML-like tags to mark different sections of the workflow.

    Parameters
    ----------
    filename : str
        The path to the Flamingo workflow file.

    Returns
    -------
    dict
        A dictionary representation of the Flamingo workflow.
    """
    with open(filename, "r") as f:
        next(f)  # Skip the first line

        settings_dict = {
            "Experiment Settings": {},
            "Camera Settings": {},
            "Stack Settings": {},
            "Start Position": {},
            "End Position": {},
            "Illumination Source": {},
            "Illumination Path": {},
        }

        current_section = ""  # Initialize the current section
        for line in f:
            if "<" in line and ">" in line:
                # Start of a new section
                current_section = line.strip()[1:-1]
            else:
                # Parse the key and value from the line
                key, value = line.strip().split("=")
                settings_dict[current_section][key.strip()] = value.strip()

        return settings_dict
