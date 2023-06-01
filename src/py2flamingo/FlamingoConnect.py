import os
import shutil
import sys
import time
from queue import Queue
from threading import Event, Thread
import functions.microscope_connect as mc
from functions.image_display import convert_to_qimage
from functions.text_file_parsing import dict_to_text, text_to_dict, workflow_to_dict
from go_to_position import go_to_position
from locate_sample import locate_sample
from PyQt5.QtCore import QSize, Qt, QTimer
from PyQt5.QtGui import QDoubleValidator, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)
from set_home import set_home
from take_snapshot import take_snapshot

from global_objects import view_snapshot, system_idle, processing_event, send_event, terminate_event, visualize_event
from global_objects import image_queue, command_queue, z_plane_queue, intensity_queue, visualize_queue, command_data_queue, stage_location_queue, other_data_queue
class FlamingoConnect:
    def __init__(self, queues_and_events):
        self.image_queue, self.command_queue, self.z_plane_queue, self.intensity_queue, self.visualize_queue = queues_and_events['queues']
        self.view_snapshot, self.system_idle, self.processing_event, self.send_event, self.terminate_event = queues_and_events['events']
        # Initialized properties
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
        self.check_folders()
        # Check if 'ZStack.txt' 'ScopeSettings 'FlamingoMetaData' files exist, otherwise prompt user to select a file
        self.check_metadata_file_and_connect()
        self.check_zstack_file()
        self.check_start_position()

    def check_folders(self):
        if not os.path.exists("workflows"):
            os.makedirs("workflows")
        if not os.path.exists("ouput_png"):
            os.makedirs("ouput_png")

    def check_start_position(self):
        file_path = os.path.join(
            "microscope_settings", f"{self.instrument_name}_start_position.txt"
        )
        if not os.path.exists(file_path):  # Check if the file does not exist
            # Show a message box with a custom message
            message_box = QMessageBox()
            message_box.setIcon(QMessageBox.Warning)
            message_box.setWindowTitle("File Not Found")
            message_box.setText(
                f"The file {self.instrument_name}_start_position.txt was not found in the 'microscope_settings' folder. \nYou will need to add the start position through the GUI to use the Find Sample funciton."
            )
            message_box.setStandardButtons(QMessageBox.Ok)
            message_box.exec_()
        else:
            positions_dict = text_to_dict(
                os.path.join(
                    "microscope_settings", f"{self.instrument_name}_start_position.txt"
                )
            )
            self.start_position = [
                float(positions_dict[self.instrument_name]["x(mm)"]),
                float(positions_dict[self.instrument_name]["y(mm)"]),
                float(positions_dict[self.instrument_name]["z(mm)"]),
                float(positions_dict[self.instrument_name]["r(°)"]),
            ]
            self.current_coordinates = [
                self.start_position[0],
                self.start_position[1],
                self.start_position[2],
                self.start_position[3],
            ]

    def check_metadata_file_and_connect(self):
        # Define the file path for the FlamingoMetaData.txt file
        file_path = os.path.join("microscope_settings", "FlamingoMetaData.txt")
        if not os.path.exists(file_path):  # Check if the file does not exist
            # Show a message box with a custom message
            message_box = QMessageBox()
            message_box.setIcon(QMessageBox.Warning)
            message_box.setWindowTitle("File Not Found")
            message_box.setText(
                "The file FlamingoMetaData.txt was not found at microscope_settings/FlamingoMetadata.txt. \nPlease locate a Metadata text file to use as the basis for your microscope (e.g. IP address, tube length). One should be generated when a workflow is manually run on the microscope."
            )
            message_box.setStandardButtons(QMessageBox.Ok)
            message_box.exec_()

            # Prompt user to select a text file
            file_dialog = QFileDialog()
            file_dialog.setWindowTitle("Select Metadata Text File")
            file_dialog.setFileMode(QFileDialog.ExistingFile)
            file_dialog.setNameFilter("Text files (*.txt)")
            if file_dialog.exec_():
                selected_file = file_dialog.selectedFiles()[
                    0
                ]  # Get the selected file path
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

        # Read the text file to determine the default values for the dialog box
        metadata_dict = text_to_dict(
            file_path
        )  # Convert the metadata text file to a dictionary
        self.IP = metadata_dict["Instrument"]["Type"]["Microscope address"].split(" ")[
            0
        ]  # Get the IP address from the dictionary
        self.port = int(
            metadata_dict["Instrument"]["Type"]["Microscope address"].split(" ")[1]
        )  # Get the port from the dictionary
        self.instrument_name = metadata_dict["Instrument"]["Type"][
            "Microscope name"
        ]  # Get the microscope name from the dictionary
        ## Currently non-functional as the text file always gives all models.
        self.instrument_type = metadata_dict["Instrument"]["Type"][
            "Microscope type"
        ]  # Get the microscope type from the dictionary
        commands = text_to_dict(
            os.path.join("src", "py2flamingo", "functions", "command_list.txt")
        )
        COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD = int(
            commands["CommandCodes.h"]["COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD"]
        )
        command_queue.put(COMMAND_CODES_COMMON_SCOPE_SETTINGS_LOAD)  # movement
        send_event.set()

        # Use the information from the FlamingoMetaData.txt file to start the connection with the correct instrument.
        nuc_client, live_client, wf_zstack, LED_on, LED_off = mc.start_connection(
            self.IP, self.port
        )
        self.connection_data = [nuc_client, live_client, wf_zstack, LED_on, LED_off]
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

    def check_zstack_file(self):
        file_path = os.path.join(
            "workflows", "ZStack.txt"
        )  # Define the file path for the ZStack.txt file
        if not os.path.exists(file_path):  # Check if the file does not exist
            # Show a message box with a custom message
            message_box = QMessageBox()
            message_box.setIcon(QMessageBox.Warning)
            message_box.setWindowTitle("File Not Found")
            message_box.setText(
                "The file ZStack.txt was not found at workflows/ZStack.txt. \nPlease locate a workflow text (workflow.txt) file to use as the basis for your settings (Laser line, laser power). One should be generated when a workflow is manually run on the microscope."
            )
            message_box.setStandardButtons(QMessageBox.Ok)
            message_box.exec_()

            # Prompt user to select a text file
            file_dialog = QFileDialog()
            file_dialog.setWindowTitle("Select Workflow Text File")
            file_dialog.setFileMode(QFileDialog.ExistingFile)
            file_dialog.setNameFilter("Text files (*.txt)")
            if file_dialog.exec_():
                selected_file = file_dialog.selectedFiles()[
                    0
                ]  # Get the selected file path
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
                QMessageBox.warning(
                    self, "Warning", "ZStack.txt file not found! Closing."
                )

        # Read the text file to determine the default values for the dialog box
        zdict = workflow_to_dict(
            file_path
        )  # Convert the workflow text file to a dictionary
        self.lasers = zdict[
            "Illumination Source"
        ]  # Get the laser sources from the dictionary
        for laser in self.lasers:
            if zdict["Illumination Source"][laser].split(" ")[1] == "1":
                self.selected_laser = (
                    laser  # Set the selected laser based on the dictionary value
                )
                self.laser_power = zdict["Illumination Source"][laser].split(" ")[
                    0
                ]  # Set the laser power based on the dictionary value

        self.lasers = [
            entry for entry in self.lasers if "laser" in entry.lower()
        ]  # Filter the lasers list based on specific condition
        self.data_storage_location = zdict["Experiment Settings"][
            "Save image drive"
        ]  # Get the data storage location from the dictionary
