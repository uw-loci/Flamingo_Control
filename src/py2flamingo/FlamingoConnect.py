import os
import shutil
from queue import Queue
from threading import Event, Thread

import py2flamingo.functions.microscope_connect as mc
from py2flamingo.utils.file_handlers import text_to_dict, workflow_to_dict
from .global_objects import (
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


def show_warning_message(warning):
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
    message_box.setWindowTitle("Something went wrong!")
    message_box.setText(warning)
    message_box.setStandardButtons(QMessageBox.Ok)
    message_box.exec_()
    return


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
        self.z_default = ""
        # Check if necessary folders exist, and create them if not
        self.check_folders()

        # Check if necessary metadata files exist, and prompt the user to select a file if not
        self.check_metadata_file_and_connect()
        self.get_initial_z_depth()
        self.check_zstack_file()
        self.check_start_position()

    def get_initial_z_depth(self):
        _, scope_settings = mc.get_microscope_settings(
            command_queue, other_data_queue, send_event
        )
        zmax = scope_settings["Stage limits"]["Soft limit max z-axis"]
        zmin = scope_settings["Stage limits"]["Soft limit min z-axis"]
        self.z_default = (
            abs(float(zmax) - float(zmin)) * 0.9
        )  # try to avoid pushing the limits which causes errors

    def check_folders(self):
        """
        Ensure that the required directories for data storage exist.
        If they do not exist, this method will create them.
        """
        required_dirs = ["workflows", "microscope_settings", "output_png"]
        for d in required_dirs:
            if not os.path.isdir(d):
                os.makedirs(d)
                print(f"Created missing directory: {d}")

    def check_metadata_file_and_connect(self):
        """
        Verify that the necessary metadata file exists, and prompt the user to select it if not present.
        If the metadata file is present, proceed to establish connection to the microscope.
        """
        # The metadata file contains instrument IP and port info
        metadata_path = os.path.join("microscope_settings", "FlamingoMetaData.txt")
        if not os.path.exists(metadata_path):
            # Prompt user to select the metadata file if not found
            dlg = QFileDialog()
            dlg.setFileMode(QFileDialog.ExistingFile)
            dlg.setNameFilter("Text files (*.txt)")
            if dlg.exec_():
                selected_files = dlg.selectedFiles()
                if selected_files:
                    shutil.copy(selected_files[0], metadata_path)
        # If metadata file now exists, read it and connect
        if os.path.exists(metadata_path):
            metadata_dict = text_to_dict(metadata_path)
            ip = metadata_dict.get("IP address")
            port = metadata_dict.get("Port")
            instrument_name = metadata_dict.get("Instrument name")
            instrument_type = metadata_dict.get("Instrument type")
            if ip and port:
                self.IP = ip
                self.port = int(port)
                self.instrument_name = instrument_name
                self.instrument_type = instrument_type
                # Try to connect to microscope
                nuc_client, live_client, wf_zstack, LED_on, LED_off = mc.start_connection(
                    self.IP, self.port
                )
                # Create threads for communication and processing
                threads = mc.create_threads(
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
                self.threads = threads
                # Prepare connection data tuple for controllers
                self.connection_data = (nuc_client, live_client, wf_zstack, LED_on, LED_off)
            else:
                show_warning_message("Invalid metadata file format: missing IP or Port.")
        else:
            show_warning_message("FlamingoMetaData.txt not found. Connection cannot be established.")

    def check_zstack_file(self):
        """
        Ensure that a ZStack workflow file exists, by copying a workflow template if necessary.
        """
        default_wf = os.path.join("workflows", "ZStack.txt")
        if not os.path.exists(default_wf):
            # Copy from a template or create a blank one
            open(default_wf, "a").close()
            print("Created default ZStack workflow file.")

    def check_start_position(self):
        """
        Verify that a start position file exists; if not, prompt the user or create a placeholder.
        """
        # The start position file name might depend on instrument or sample
        # For now, just ensure some file exists as a placeholder.
        files = [f for f in os.listdir("microscope_settings") if f.endswith("_start_position.txt")]
        if not files:
            placeholder = os.path.join("microscope_settings", "default_start_position.txt")
            open(placeholder, "a").close()
            print("Created default start position file.")
