# Includes functions to read and write text files specifically designed for the Flamingo
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

# Create a dictionary of the microscope settings from a text file
# def settings_to_dict(filename):
#     #print('workflow to dict '+filename)
#     with open(filename, 'r') as f:
#         # Create an empty dictionary to store the settings
#         settings_dict = {'Filter wheel encoder counts': {},
#                          'Filter wheel position assignments': {},
#                          'Illumination settings': {},
#                          'Stage limits': {},
#                          'Camera overlap settings': {},
#                          'LED settings': {},
#                          'Type': {}
#                          }

#         # Read the rest of the lines and parse them
#         current_section = ''
#         for line in f:
#             # Check if this line marks the start of a new section
#             if '<' in line and '>' in line:
#                 current_section = line.strip()[1:-1]
#             else:
#                 # Parse the key and value from the line
#                 key, value = line.strip().split('=')
#                 settings_dict[current_section][key.strip()] = value.strip()

#         return settings_dict
    
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

