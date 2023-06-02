import os
import shutil
from queue import Queue
from threading import Event, Thread

import functions.microscope_connect as mc
from functions.text_file_parsing import text_to_dict, workflow_to_dict
from global_objects import (
    command_data_queue,
    command_queue,
    image_queue,
    intensity_queue,
    other_data_queue,
    processing_event,
    send_event,
    stage_location_queue,
    system_idle,
    terminate_event,
    view_snapshot,
    visualize_event,
    visualize_queue,
    z_plane_queue,
)
from PyQt5.QtWidgets import QFileDialog, QMessageBox


class FlamingoConnect:
    """
    This class is responsible for establishing and managing the connection to the microscope. It also initializes various properties related to the microscope, such as its IP address, port, instrument name, and type, as well as the start position, current coordinates, available lasers, selected laser, laser power, and data storage location.

    Attributes
    ----------
    image_queue : queue.Queue
        A queue for storing images from the microscope.
    command_queue : queue.Queue
        A queue for storing commands to be sent to the microscope.
    z_plane_queue : queue.Queue
        A queue for storing information about the z-plane of the microscope.
    intensity_queue : queue.Queue
        A queue for storing intensity data from the microscope.
    visualize_queue : queue.Queue
        A queue for storing data to be visualized.
    view_snapshot : threading.Event
        An event that signals when a snapshot should be viewed.
    system_idle : threading.Event
        An event that signals when the system is idle.
    processing_event : threading.Event
        An event that signals when the system is processing data.
    send_event : threading.Event
        An event that signals when data should be sent to the microscope.
    terminate_event : threading.Event
        An event that signals when the program should be terminated.
    IP : str
        The IP address of the microscope.
    port : int
        The port number for the microscope connection.
    instrument_name : str
        The name of the microscope instrument.
    instrument_type : str
        The type of the microscope instrument.
    connection_data : tuple
        A tuple containing data about the microscope connection.
    threads : tuple
        A tuple containing the threads used for the microscope connection.
    start_position : list
        The starting position of the microscope, represented as a list of four strings.
    current_coordinates : list
        The current coordinates of the microscope, represented as a list of four strings.
    lasers : list
        A list of lasers available for use with the microscope.
    selected_laser : str
        The laser currently selected for use.
    laser_power : str
        The power level of the laser.
    data_storage_location : str
        The location where data is stored.
    """

    def __init__(self, queues_and_events):
        # Get queues and events defined in global_objects.py
        (
            self.image_queue,
            self.command_queue,
            self.z_plane_queue,
            self.intensity_queue,
            self.visualize_queue,
        ) = queues_and_events["queues"]
        (
            self.view_snapshot,
            self.system_idle,
            self.processing_event,
            self.send_event,
            self.terminate_event,
        ) = queues_and_events["events"]

        # Initialize properties
        self.IP = None
        self.port = None
        self.instrument_name = None
        self.instrument_type = None
        self.connection_data = None
        self.threads = None
        self.start_position = ["", "", "", ""]
        self.current_coordinates = []
        self.lasers = []
        self.selected_laser = ""
        self.laser_power = ""
        self.data_storage_location = ""

        # Check if necessary folders exist, and create them if not
        self.check_folders()

        # Check if necessary metadata files exist, and prompt the user to select a file if not
        self.check_metadata_file_and_connect()

        self.check_zstack_file()
        self.check_start_position()

    def check_folders(self):
        """
        Checks if the necessary folders 'workflows' and 'output_png' exist in the current directory. If they do not exist, the method creates them.
        """
        # Check if the 'workflows' folder exists, and create it if not
        if not os.path.exists("workflows"):
            os.makedirs("workflows")

        # Check if the 'output_png' folder exists, and create it if not
        if not os.path.exists("output_png"):
            os.makedirs("output_png")

    def check_start_position(self):
        """
        Checks if the start position file for the microscope exists. If it does not exist, a warning message is displayed to the user. If it does exist, the start position and current coordinates are read from the file and stored in the corresponding attributes.
        """
        # Define the path to the start position file
        file_path = os.path.join(
            "microscope_settings", f"{self.instrument_name}_start_position.txt"
        )

        # Check if the file does not exist
        if not os.path.exists(file_path):
            # Show a message box with a custom message
            message_box = QMessageBox()
            message_box.setIcon(QMessageBox.Warning)
            message_box.setWindowTitle("File Not Found")
            message_box.setText(
                f"The file {self.instrument_name}_start_position.txt was not found in the 'microscope_settings' folder. \nYou will need to add the start position through the GUI to use the Find Sample function."
            )
            message_box.setStandardButtons(QMessageBox.Ok)
            message_box.exec_()
        else:
            # If the file exists, read the start position from the file
            positions_dict = text_to_dict(file_path)

            # Store the start position and current coordinates
            self.start_position = [
                float(positions_dict[self.instrument_name]["x(mm)"]),
                float(positions_dict[self.instrument_name]["y(mm)"]),
                float(positions_dict[self.instrument_name]["z(mm)"]),
                float(positions_dict[self.instrument_name]["r(°)"]),
            ]
            self.current_coordinates = self.start_position.copy()

    def check_file_exists(self, file_path):
        """
        Checks whether a specified file exists.

        This function uses the os library's path.exists method to check whether a file at a given path exists.

        Parameters
        ----------
        file_path : str
            The path of the file to check.

        Returns
        -------
        bool
            True if the file exists, False otherwise.
        """
        return os.path.exists(file_path)

    def show_warning_message(self, warning):
        """
        Displays a warning message box.

        This function uses the QMessageBox class from the PyQt5.QtWidgets library to display a message box
        with a specified warning message.

        Parameters
        ----------
        warning : str
            The warning message to display.
        """
        message_box = QMessageBox()
        message_box.setIcon(QMessageBox.Warning)
        message_box.setWindowTitle("File Not Found")
        message_box.setText(warning)
        message_box.setStandardButtons(QMessageBox.Ok)
        message_box.exec_()

    def prompt_user_for_file(self):
        """
        Prompts the user to select a file.

        This function uses the QFileDialog class from the PyQt5.QtWidgets library to open a file dialog
        where the user can select a file. The dialog is configured to only show existing text files.

        Returns
        -------
        file_path : str
            The path of the selected file, or an empty string if no file was selected.
        """
        file_dialog = QFileDialog()
        file_dialog.setWindowTitle("Select Metadata Text File")
        file_dialog.setFileMode(QFileDialog.ExistingFile)
        file_dialog.setNameFilter("Text files (*.txt)")
        if file_dialog.exec_():
            file_path = file_dialog.selectedFiles()[0]
            return file_path
        else:
            return ""

    def process_selected_file(self, file_dialog, file_path):
        """
        Processes a file selected by the user.

        This function first checks whether a file has been selected in the provided file dialog. If a file
        has been selected, it is copied to a specified path. If an error occurs during the copy operation,
        a warning message is displayed.

        Parameters
        ----------
        file_dialog : QFileDialog
            The file dialog in which the user selects a file.
        file_path : str
            The path to copy the selected file to.
        """
        if file_dialog.exec_():
            selected_file = file_dialog.selectedFiles()[0]  # Get the selected file path
            os.makedirs(
                "microscope_settings", exist_ok=True
            )  # Create the 'microscope_settings' directory if it doesn't exist
            try:
                shutil.copy(
                    selected_file, file_path
                )  # Copy the selected file to the 'microscope_settings' directory
            except OSError as e:
                QMessageBox.warning(self, "Error", str(e))
        else:
            QMessageBox.warning(
                self, "Warning", "FlamingoMetaData.txt file not found! Closing."
            )

    def read_metadata(self, file_path):
        """
        Reads microscope metadata from a file.

        This function uses the text_to_dict function to convert the contents of a specified text file
        into a dictionary. The IP, port, instrument name, and instrument type attributes of the class
        are then set using the data from this dictionary.

        Parameters
        ----------
        file_path : str
            The path to the file containing the metadata.

        Returns
        -------
        dict
            A dictionary representation of the file's content.
        """
        metadata_dict = text_to_dict(file_path)

        self.IP = metadata_dict["Instrument"]["Type"]["Microscope address"].split(" ")[
            0
        ]
        self.port = int(
            metadata_dict["Instrument"]["Type"]["Microscope address"].split(" ")[1]
        )
        self.instrument_name = metadata_dict["Instrument"]["Type"]["Microscope name"]
        self.instrument_type = metadata_dict["Instrument"]["Type"]["Microscope type"]

        return metadata_dict

    def connect_to_microscope(self, metadata_dict):
        """
        Establishes a connection to the microscope.

        This function uses the start_connection function from the mc (microscope_connect) module
        to establish a connection to the microscope and then create relevant threads for handling
        the connection and processing data. The connection data and threads are stored as class attributes.

        Parameters
        ----------
        metadata_dict : dict
            A dictionary containing the metadata information necessary for establishing the connection.
        """
        # Start connection and get relevant client objects and commands
        nuc_client, live_client, wf_zstack, LED_on, LED_off = mc.start_connection(
            self.IP, self.port
        )
        self.connection_data = [nuc_client, live_client, wf_zstack, LED_on, LED_off]

        # Create relevant threads for listening, sending and processing data
        (
            live_listen_thread_var,
            command_listen_thread_var,
            send_thread_var,
            processing_thread_var,
        ) = mc.create_threads(
            nuc_client,
            live_client,
            other_data_queue,
            image_queue,
            command_queue,
            z_plane_queue,
            intensity_queue,
            visualize_queue,
            system_idle,
            processing_event,
            send_event,
            terminate_event,
            command_data_queue,
            stage_location_queue,
        )
        self.threads = [
            live_listen_thread_var,
            command_listen_thread_var,
            send_thread_var,
            processing_thread_var,
        ]

    def check_metadata_file_and_connect(self):
        """
        Checks for a metadata file and establishes a connection to the microscope.

        This function first checks whether a file named "FlamingoMetaData.txt" exists in the
        "microscope_settings" directory. If it does not, the user is warned and asked to select a metadata file,
        which is then processed. If the file exists, it is read and used to establish a connection to the microscope.
        """
        file_path = os.path.join("microscope_settings", "FlamingoMetaData.txt")
        if not self.check_file_exists(file_path):
            warning = "The file FlamingoMetaData.txt was not found at microscope_settings/FlamingoMetadata.txt. \nPlease locate a Metadata text file to use as the basis for your microscope (e.g. IP address, tube length). One should be generated when a workflow is manually run on the microscope."
            self.show_warning_message(warning)
            file_dialog = self.prompt_user_for_file()
            self.process_selected_file(file_dialog, file_path)
        else:
            metadata_dict = self.read_metadata(file_path)
            self.connect_to_microscope(metadata_dict)

    def prompt_user_for_zstack_file(self):
        """
        Prompts the user to select a ZStack file.

        This function uses the QFileDialog class from the PyQt5.QtWidgets library to open a file dialog
        where the user can select a ZStack file. The dialog is configured to only show existing text files.

        Returns
        -------
        file_dialog : QFileDialog
            The QFileDialog object used for selecting a file.
        """
        file_dialog = QFileDialog()
        file_dialog.setWindowTitle("Select Workflow Text File")
        file_dialog.setFileMode(QFileDialog.ExistingFile)
        file_dialog.setNameFilter("Text files (*.txt)")
        return file_dialog

    def process_selected_zstack_file(self, file_dialog, file_path):
        """
        Processes a ZStack file selected by the user.

        This function first checks whether a file has been selected in the provided file dialog. If a file
        has been selected, it is copied to a specified path. If an error occurs during the copy operation,
        a warning message is displayed.

        Parameters
        ----------
        file_dialog : QFileDialog
            The file dialog in which the user selects a file.
        file_path : str
            The path to copy the selected file to.
        """
        if file_dialog.exec_():
            selected_file = file_dialog.selectedFiles()[0]  # Get the selected file path
            os.makedirs(
                "workflows", exist_ok=True
            )  # Create the 'workflows' directory if it doesn't exist
            try:
                shutil.copy(
                    selected_file, file_path
                )  # Copy the selected file to the 'workflows' directory
            except OSError as e:
                QMessageBox.warning(self, "Error", str(e))
        else:
            QMessageBox.warning(self, "Warning", "ZStack.txt file not found! Closing.")

    def read_workflow(self, file_path):
        """
        Reads a workflow file and sets relevant class attributes.

        This function reads a workflow file at a given file path, extracts the information about
        lasers and data storage location from the file, and sets the corresponding class attributes.

        Parameters
        ----------
        file_path : str
            The path of the workflow file to read.
        """
        # Convert the workflow text file into a dictionary
        zdict = workflow_to_dict(file_path)

        # Extract illumination source information and update lasers, selected_laser, and laser_power attributes
        self.lasers = zdict["Illumination Source"]
        for laser in self.lasers:
            if zdict["Illumination Source"][laser].split(" ")[1] == "1":
                self.selected_laser = laser
                self.laser_power = zdict["Illumination Source"][laser].split(" ")[0]

        # Update the lasers attribute to only include entries with the word "laser"
        self.lasers = [entry for entry in self.lasers if "laser" in entry.lower()]

        # Extract and update data storage location
        self.data_storage_location = zdict["Experiment Settings"]["Save image drive"]

    def check_zstack_file(self):
        """
        Checks for a ZStack file and reads it if found.

        This function first checks whether a file named "ZStack.txt" exists in the
        "workflows" directory. If it does not, the user is warned and asked to select a workflow file,
        which is then processed. If the file exists, it is read and the relevant class attributes are updated.
        """
        file_path = os.path.join("workflows", "ZStack.txt")
        if not self.check_file_exists(
            file_path
        ):  # Using the previously defined function
            warning = "The file ZStack.txt was not found at workflows/ZStack.txt. \nPlease locate a workflow text (workflow.txt) file to use as the basis for your settings (Laser line, laser power). One should be generated when a workflow is manually run on the microscope."
            self.show_warning_message(warning)
            file_dialog = self.prompt_user_for_zstack_file()
            self.process_selected_zstack_file(file_dialog, file_path)
        else:
            self.read_workflow(file_path)
