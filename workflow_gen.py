# Define variables with values
import datetime
# create an option to read in a previous workflow but make minor changes

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


def generate_workflow_file( source = None,
                filename = 'workflow_settings.txt',
                save_image_directory="RemoteTest1",
                comments="BestExperimentEver",
                plane_spacing_um=2.5,
                frame_rate_fps=40.003200,
                exposure_time_us=24998,
                duration="00:00:00:00",
                interval="00:00:00:00",
                sample="",
                num_angles="",
                angle_step_size="",
                region="",
                save_image_drive="/media/deploy/MSN_LS",
                save_max_projection=False,
                display_max_projection=False,
                save_image_data_tiff=True,
                save_image_data_raw=False,
                save_to_subfolders=False,
                workflow_live_view_enabled=False,
                camera_exposure_time_us="",
                aoi_width="",
                aoi_height="",
                stack_index=1,
                change_z=0.0,
                num_planes=1,
                num_planes_saved="",
                stage_velocity=5.0,
                stack_file_name="stack001",
                c1capture=100,
                c2capture=100,
                start_pos_x=14.694,
                start_pos_y=6.278,
                start_pos_z=13.528,
                start_pos_angle=0,
                end_pos_x=14.694,
                end_pos_y=6.278,
                end_pos_z=13.528,
                end_pos_angle=30.006,
                laser_1_power="0.00 0",
                laser_2_power="0.00 0",
                laser_3_power="0.00 0",
                laser_4_power="6.45 0",
                led_rgb_board="20.56 1",
                led_selection="1 0",
                left_path="ON 1",
                right_path="OFF 0",
                led_dac="38894 0"):
    """
    This function creates the workflow file to be immediately implemented in cc_loop.py
    """
    if source:
        #incomplete at this time as might be unnecessary - but ideally can get all of the values through wf_to_dict
        date_time_stamp

    now = datetime.datetime.now()
    # Format date and time as string
    date_time_stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    # Generate file
    workflowtext= f'''<Workflow Settings>
        <Experiment Settings>
        Plane spacing (um) = {plane_spacing_um}
        Frame rate (f/s) = {frame_rate_fps}
        Exposure time (us) = {exposure_time_us}
        Duration (dd:hh:mm:ss) = {duration}
        Interval (dd:hh:mm:ss) = {interval}
        Sample = {sample}
        Number of angles = {num_angles}
        Angle step size = {angle_step_size}
        Region = {region}
        Save image drive = {save_image_drive}
        Save image directory = {save_image_directory}
        Comments = {comments}
        Save max projection = {save_max_projection}
        Display max projection = {display_max_projection}
        Save image data in tiff format = {save_image_data_tiff}
        Save image data in raw format = {save_image_data_raw}
        Save to subfolders = {save_to_subfolders}
        Work flow live view enabled = {workflow_live_view_enabled}
        </Experiment Settings>
        <Camera Settings>
        Exposure time (us) = {camera_exposure_time_us}
        Frame rate (f/s) =    
        AOI width = {aoi_width}
        AOI height = {aoi_height}
        </Camera Settings>
        <Stack Settings>
        Stack index = {stack_index}
        Change in Z axis (mm) = {change_z}
        Number of planes = {num_planes}
        Number of planes saved = {num_planes_saved}
        Stage velocity (mm/s) = {stage_velocity}
        Date time stamp = {date_time_stamp}
        Stack file name = {stack_file_name}
        Camera 1 capture percentage = {c1capture}
        Camera 1 capture mode (0 full, 1 from front, 2 from back, 3 none) = 0
        Camera 1 capture range = 
        Camera 2 capture percentage = {c2capture}
        Camera 2 capture mode (0 full, 1 from front, 2 from back, 3 none) = 0
        Camera 2 capture range = 
        Stack option = ZStack
        Stack option settings 1 = 
        Stack option settings 2 = 
        </Stack Settings>
        <Start Position>
        X (mm) = {start_pos_x}
        Y (mm) = {start_pos_y}
        Z (mm) = {start_pos_z}
        Angle (degrees) = {start_pos_angle}
        </Start Position>
        <End Position>
        X (mm) = {end_pos_x}
        Y (mm) = {end_pos_y}
        Z (mm) = {end_pos_z}
        Angle (degrees) = {end_pos_angle}
        </End Position>
        <Illumination Source>
        Laser 1 640 nm = {laser_1_power}
        Laser 2 561 nm = {laser_2_power}
        Laser 3 488 nm = {laser_3_power}
        Laser 4 405 nm = {laser_4_power}
        LED_RGB_Board = {led_rgb_board}
        LED selection = {led_selection}
        LED DAC = {led_dac}
        </Illumination Source>
        <Illumination Path>
        Left path = {left_path}
        Right path = {right_path}
        </Illumination Path>
    </Workflow Settings>'''

    with open(filename, 'w') as file:
        file.write(workflowtext)
    print("XML file 'workflow_settings.txt' has been generated successfully.")