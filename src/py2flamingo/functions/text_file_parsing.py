# Includes functions to read and write text files specifically designed for the Flamingo
# create an option to read in a previous workflow but make minor changes
import csv
import os
import re
from typing import Sequence


def dict_append_workflow(file_path: str, workflow_dict: dict):
    """
    Appends a dictionary of Flamingo settings to an existing Flamingo workflow file.

    This function takes a dictionary of Flamingo settings and appends it to an existing Flamingo workflow file format.
    The settings are written in the format "key = value", with XML-like tags representing sections and nested sections.

    Parameters
    ----------
    file_path : str
        The path to the existing Flamingo workflow file.
    workflow_dict : dict
        A dictionary containing the Flamingo settings to append.

    Returns
    -------
    None
        This function does not return anything but appends the Flamingo workflow settings to the specified file.
    """
    with open(file_path, "a", newline="\n") as f:
        # Start with the <Workflow Settings> tag
        f.write("\n<Workflow Settings>\n")

        # Loop over each section in the settings dictionary
        for section, settings in workflow_dict.items():
            # Add a header tag for the current section
            f.write(f"    <{section}>\n")
            # Loop over each key-value pair in the current section's settings dictionary
            for key, value in settings.items():
                # Add the key-value pair to the file with proper indentation
                f.write(f"        {key} = {value}\n")
            # Add a closing tag for the current section
            f.write(f"    </{section}>\n")

        # Add the closing tag for the <Workflow Settings> tag
        f.write("</Workflow Settings>\n")


def save_points_to_csv(sample_name: str, points_dict, description: str = None):
    """
    Save points from a dictionary to a CSV file.

    Parameters
    ----------
    description : str
        There may be multiple points of interest for a given sample, this string is to differentiate them.
    sample_name : str
        The name of the sample, used to name the output file.
    points_dict : dict
        A dictionary where each key is "bounds {r}" and each value is another dictionary containing the x, y, z, and r values for the corresponding point.

    The function writes a CSV file with columns for 'bounds', 'x (mm)', 'y (mm)', 'z (mm)', and 'r (°)'.
    """

    # Ensure there are at least two entries in the points_dict
    if len(points_dict) < 2:
        print("Error: Need at least two points to compute angle step size.")
        return

    # Get the keys (bounds) of the first two entries
    bounds_keys = list(points_dict.keys())
    first_key, second_key = bounds_keys[0], bounds_keys[1]

    # Extract the 'r' values and compute the angle step size
    first_r = float(points_dict[first_key]["r (°)"])
    second_r = float(points_dict[second_key]["r (°)"])
    angle_step_size_deg = second_r - first_r

    # Define the location of the output file
    file_location = os.path.join(
        "sample_txt",
        f"{sample_name}",
        f"{sample_name}_{description}_points_anglestep_{angle_step_size_deg}.csv",
    )

    # Open the output file in write mode
    with open(file_location, "w", newline="\n") as f:
        # Create a CSV writer
        writer = csv.writer(f)
        # Write the header row to the CSV file
        writer.writerow(["bounds", "x (mm)", "y (mm)", "z (mm)", "r (°)"])
        # Iterate over the points in the dictionary
        for bounds, point in points_dict.items():
            # Write each point to a new row in the CSV file
            writer.writerow([bounds] + list(point.values()))


def set_workflow_type(
    dict, experiment_type: str, overlap: float, overlap_y: float = None
):
    """
    TODO: figure out the rest
    Modify a dict file to perform a non-standard workflow, the standard being a single Zstack
    Options include "ZStack" "ZStack Movie" "ZStack API" "Tile" "ZSweep" "OPT" "OPT ZStacks"
    overlap: the overlap in percent. If this is the only value passed, it is used for both X and Y
    overlap_y: if passed, this overlap is used in Y
    """
    if overlap_y is None:
        overlap_y = overlap
    dict["Stack Settings"]["Stack option"] = experiment_type
    dict["Stack Settings"]["Stack option settings 1"] = overlap
    dict["Stack Settings"]["Stack option settings 2"] = overlap_y
    return dict


def calculate_zplanes(wf_dict, z_search_depth, framerate, plane_spacing=None):
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
    # if no plane spacing is specified, use what is already in the workflow
    if plane_spacing is None:
        plane_spacing = float(wf_dict["Experiment Settings"]["Plane spacing (um)"])

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
    workflow_dict["Illumination Source"][
        laser_channel
    ] = f"{str(laser_setting)} {int(laser_on)}"

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
        coordinates = [float(coord) for coord in point.values()]
        points.append(coordinates)

    return points


def check_coordinate_limits(workflow_input):
    """
    Validates coordinates within a workflow or a list of workflows against predefined limits.

    This function accepts either a single workflow dictionary or a list of workflow dictionaries. 
    For each workflow, it checks the 'start' and 'end' coordinates for the X, Y, and Z axes against 
    the limits specified in a settings file. If any coordinate falls outside the limits, a ValueError 
    is raised.

    The settings file should be located at 'microscope_settings/ScopeSettings.txt' and is expected 
    to be in a format compatible with the `text_to_dict` function.

    Parameters:
    ----------
    workflow_input (Union[dict, List[dict]]): A dictionary or list of dictionaries, each containing 
                                             'start' and 'end' coordinates for the X, Y, and Z axes.

    Returns:
    -------
    bool: True if all coordinates in all workflows are strictly within the limits, False otherwise.

    Raises:
    ------
    ValueError: If a 'start' or 'end' coordinate is outside the limits.
    TypeError: If the input is neither a dictionary nor a list of dictionaries.
    """

    # Determine the type of the input: either a single dictionary or a list of dictionaries
    if isinstance(workflow_input, dict):
        workflow_list = [
            workflow_input
        ]  # Convert single dictionary to a list for uniform processing
    elif isinstance(workflow_input, list):
        workflow_list = workflow_input
    else:
        raise TypeError("Input must be a dictionary or a list of dictionaries")

    # Load the scope settings from a predefined settings file
    scope_settings = text_to_dict(
        os.path.join("microscope_settings", "ScopeSettings.txt")
    )

    # Loop over each workflow in the list
    for workflow_dict in workflow_list:

        # Loop over each axis (X, Y, Z) to validate the coordinates
        for axis in ["x", "y", "z"]:

            # Extract coordinate limits for the current axis from the settings
            min_limit = float(
                scope_settings["Stage limits"][f"Soft limit min {axis}-axis"]
            )
            max_limit = float(
                scope_settings["Stage limits"][f"Soft limit max {axis}-axis"]
            )

            # Extract 'start' and 'end' coordinates for the current axis from the workflow
            workflow_start = float(
                workflow_dict["Start Position"][f"{axis.upper()} (mm)"]
            )
            workflow_end = float(workflow_dict["End Position"][f"{axis.upper()} (mm)"])

            # Check and validate that the coordinates are within the predefined limits
            if not (
                min_limit < workflow_start < max_limit
                and min_limit < workflow_end < max_limit
            ):
                raise ValueError(
                    f"The {axis}-axis workflow coordinates are outside the limits. "
                    f"Start: {workflow_start}, End: {workflow_end}, "
                    f"Min Limit: {min_limit}, Max Limit: {max_limit}"
                )

    # If all coordinates of all workflows pass validation, return True
    return True


def dict_positions(
    workflow_dict: dict,
    xyzr: Sequence[float],
    xyzr2: Sequence[float] = None,
    zEnd: float = None,
    save_with_data=False,
    get_zstack=False,
):  # sourcery skip: extract-method, remove-unnecessary-cast
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
        workflow_dict["Stack Settings"]["Change in Z axis (mm)"] = abs(
            float(z) - float(z2)
        )
    elif zEnd is not None:
        workflow_dict["End Position"]["X (mm)"] = float(x)
        workflow_dict["End Position"]["Y (mm)"] = float(y)
        workflow_dict["End Position"]["Z (mm)"] = float(zEnd)
        workflow_dict["End Position"]["Angle (degrees)"] = float(r)
        workflow_dict["Stack Settings"]["Change in Z axis (mm)"] = abs(
            float(z) - float(zEnd)
        )
    else:
        raise ValueError("Either xyzr2 or zEnd must be provided.")

    if save_with_data:
        workflow_dict["Experiment Settings"][
            "Comments"
        ] = f"Snapshot at {x}, {y}, {z}, {r}"
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
    return filename.upper() not in [
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    ]


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


def workflow_to_dict(filename: str):
    """
    Converts a Flamingo workflow file into a dictionary or a list of dictionaries.

    This function reads a Flamingo workflow file and converts it into a dictionary or list of dictionaries, 
    where each dictionary corresponds to a single <Workflow Settings> block.
    The workflow file should follow a specific format where each line represents a key-value pair in the format "key = value".
    The file should also contain XML-like tags to mark different sections of the workflow.

    Parameters
    ----------
    filename : str
        The path to the Flamingo workflow file.

    Returns
    -------
    Union[dict, list]
        A dictionary (for a single <Workflow Settings> block) or 
        a list of dictionaries (for multiple <Workflow Settings> blocks).
    """
    with open(filename, "r") as f:
        all_workflows = []  # List to store all workflow dictionaries

        while True:  # Loop until end of file
            line = f.readline()
            if not line:
                break  # End of file

            # Check for the start of a new <Workflow Settings> block
            if "<Workflow Settings>" in line:
                settings_dict = {}
                current_section = None  # Initialize the current section to None
                while "</Workflow Settings>" not in line:  # Loop until end of the block
                    line = f.readline()
                    if not line:
                        break  # End of file
                    if "<" in line and ">" in line:
                        # Start of a new section
                        current_section = line.strip()[1:-1]
                        settings_dict[current_section] = {}
                    elif (
                        "=" in line and current_section
                    ):  # Ensure current_section is not None
                        # Parse the key and value from the line
                        key, value = line.strip().split("=")
                        settings_dict[current_section][key.strip()] = value.strip()
                all_workflows.append(settings_dict)

    return all_workflows[0] if len(all_workflows) == 1 else all_workflows


# def workflow_to_dict(filename: str) -> dict:
#     """
#     Converts a Flamingo workflow file into a dictionary.

#     This function reads a Flamingo workflow file and converts it into a dictionary format.
#     The workflow file should follow a specific format where each line represents a key-value pair in the format "key = value".
#     The file should also contain XML-like tags to mark different sections of the workflow.

#     Parameters
#     ----------
#     filename : str
#         The path to the Flamingo workflow file.

#     Returns
#     -------
#     dict
#         A dictionary representation of the Flamingo workflow.
#     """
#     with open(filename, "r") as f:
#         next(f)  # Skip the first line

#         settings_dict = {
#             "Experiment Settings": {},
#             "Camera Settings": {},
#             "Stack Settings": {},
#             "Start Position": {},
#             "End Position": {},
#             "Illumination Source": {},
#             "Illumination Path": {},
#         }

#         current_section = ""  # Initialize the current section
#         for line in f:
#             if "<" in line and ">" in line:
#                 # Start of a new section
#                 current_section = line.strip()[1:-1]
#             else:
#                 # Parse the key and value from the line
#                 key, value = line.strip().split("=")
#                 settings_dict[current_section][key.strip()] = value.strip()

#         return settings_dict


def points_to_dict(points):
    """
    Convert a list of points to a dictionary.

    Parameters
    ----------
    points : list of lists
        A list of points, where each point is a list of four floats [x, y, z, r].

    Returns
    -------
    dict
        A dictionary where each key is "bounds {r}" and each value is another dictionary containing the x, y, z, and r values for the corresponding point.
    """
    bounding_dict = {}
    for point in points:
        x, y, z, r = point
        bounding_dict[f"bounds {r}"] = {
            "x (mm)": x,
            "y (mm)": y,
            "z (mm)": z,
            "r (°)": r,
        }
    return bounding_dict


def save_ellipse_params(sample_name, params, angle_step_size_deg, xyzr):
    """
    Save the parameters of the ellipse fit to a text file.

    The parameters are saved in a dictionary format with the keys "h", "k", "a", and "b". 
    The output file is named with the pattern "{sample_name}_{angle_step_size_deg}_deg_ellipse_params.txt" 
    and saved to the "sample_txt/{sample_name}" directory.

    Parameters
    ----------
    sample_name : str
        The name of the sample which the ellipse parameters are associated with.
    params : tuple
        A tuple containing the parameters (h, k, a, b) of the fitted ellipse. 
        h, k: the coordinates of the center of the ellipse.
        a, b: the semi-major and semi-minor axes of the ellipse, respectively.
    angle_step_size_deg : int or float
        The increment in degrees that was used to collect data for the ellipse fit.

    Returns
    -------
    None
    """

    # Create a dictionary with the sample name as the key and the ellipse parameters as the values.
    ellipse_parameters = {
        "Ellipse parameters": {
            "h": params[0],  # The x-coordinate of the center of the ellipse
            "k": params[1],  # The y-coordinate of the center of the ellipse
            "a": params[2],  # The length of the semi-major axis
            "b": params[3],  # The length of the semi-minor axis
        },
        "Additional information": {
            "Angle step size (deg)": angle_step_size_deg,
            "Y position (mm)": xyzr[1],
        },
    }

    # Define the path for the output text file.
    # The file will be saved in the "sample_txt/{sample_name}" directory,
    # and the filename will follow the pattern "{sample_name}_{angle_step_size_deg}_deg_ellipse_params.txt".
    output_path = str(
        os.path.join(
            "sample_txt", f"{sample_name}", f"{sample_name}_ellipse_params.txt",
        )
    )

    # Save the parameters to a text file using the `dict_to_text` function.
    dict_to_text(output_path, ellipse_parameters)
