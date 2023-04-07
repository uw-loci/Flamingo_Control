
def parse_input_file(filename):
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

# def parse_workflow(file_path):
#     """
#     Parses a text file into a dictionary of name-value pairs.

#     Args:
#         file_path (str): The path to the text file.

#     Returns:
#         dict: A dictionary containing the parsed name-value pairs.
#     """
#     name_value_pairs = {}  # Create an empty dictionary to store the name-value pairs
#     with open(file_path, 'r') as file:  # Open the file for reading
#         current_section = None  # Initialize the current section to None
#         for line in file:  # Loop through each line in the file
#             line = line.strip()  # Remove leading and trailing whitespace
#             if line.startswith('<'):  # If the line starts with '<', it indicates a new section
#                 current_section = line[1:-1]  # Set the current section to the section name
#             elif '=' in line and current_section:  # If the line contains '=', it indicates a name-value pair
#                 name, value = map(str.strip, line.split('='))  # Split the line on '=' and strip whitespace from the name and value
#                 name_value_pairs[current_section + '.' + name] = value  # Combine the section name and name to form the dictionary key, and store the value as the dictionary value
#     return name_value_pairs

# def dict_to_workflow(name_value_pairs, file_path):
#     """
#     Writes a dictionary of name-value pairs to a text file in the format shown in the example.

#     Args:
#         name_value_pairs (dict): A dictionary containing the name-value pairs to write to the file.
#         file_path (str): The path to the text file to write.

#     Returns:
#         None.
#     """
#     with open(file_path, 'w') as file:  # Open the file for writing
#         file.write('<Workflow Settings>\n')  # Write the section header for Workflow Settings
#         for key, value in name_value_pairs.items():  # Loop through the name-value pairs in the dictionary
#             section, name = key.split('.')  # Split the key into the section name and the name of the value
#             if section != 'Workflow Settings':  # If the section is not Workflow Settings, write the section header
#                 file.write(f'\n<{section}>\n')
#             file.write(f'{name} = {value}\n')  # Write the name-value pair to the file
#         file.write('</Workflow Settings>')  # Write the closing tag for Workflow Settings
# def create_text_file(settings_dict, filename):
#     with open(filename, 'w') as f:
#         f.write('<Workflow Settings>\n')
#         f.write('\t<Experiment Settings>\n')
#         for key, value in settings_dict['Experiment Settings'].items():
#             f.write(f'\t\t{key} = {value}\n')
#         f.write('\t</Experiment Settings>\n')
#         f.write('\t<Camera Settings>\n')
#         for key, value in settings_dict['Camera Settings'].items():
#             f.write(f'\t\t{key} = {value}\n')
#         f.write('\t</Camera Settings>\n')
#         f.write('\t<Stack Settings>\n')
#         for key, value in settings_dict['Stack Settings'].items():
#             f.write(f'\t\t{key} = {value}\n')
#         f.write('\t</Stack Settings>\n')

#         f.write('</Workflow Settings>\n')
def create_text_file(file_name, settings_dict):
    output = '<Workflow Settings>\n'

    for section, settings in settings_dict.items():
        #print(section)
        output += f'\t<{section}>\n'
        for key, value in settings.items():
            output += f'\t{key} = {value}\n'
        output += f'\t</{section}>\n'

    output += '</Workflow Settings>'

    with open(file_name, 'w') as f:
        f.write(output)

dict = parse_input_file('SingleImageWorkflow.txt')

# for key, value in dict.items():
#     print(f"{key}: {value}")

create_text_file("test_workflow_output.txt", dict)
# for key, value in dict.items():
#     print(f"{key}: {value}")

# plane_spacing_um = 2.5
# frame_rate_fps = 40.003200
# exposure_time_us = 24998
# duration = "00:00:00:00"
# interval = "00:00:00:00"
# sample = ""
# num_angles = ""
# angle_step_size = ""
# region = ""
# save_image_drive = "/media/deploy/MSN_LS"
# save_max_projection = False
# display_max_projection = False
# save_image_data_tiff = True
# save_image_data_raw = False
# save_to_subfolders = False
# workflow_live_view_enabled = False
# camera_exposure_time_us = ""
# aoi_width = ''
# aoi_height = ''
# stack_index = 1
# change_z = 0.0
# num_planes = 1
# num_planes_saved = ''
# stage_velocity = 5.0
# stack_file_name = "stack001"
# c1capture = 100
# c2capture = 100
# start_pos_x = 14.694
# start_pos_y = 6.278
# start_pos_z = 13.528
# start_pos_angle = 0
# end_pos_x = 14.694
# end_pos_y = 6.278
# end_pos_z = 13.528
# end_pos_angle = 30.006
# laser_1_power = '0.00 0'
# laser_2_power = '0.00 0'
# laser_3_power = '0.00 0'
# laser_4_power = '6.45 0'
# led_rgb_board = "20.56 1"
# led_selection = "1 0"
# left_path = 'ON 1'
# right_path = 'OFF 0'
# led_dac = '38894 0'