import os
import shutil
import sys
import time
from queue import Queue
from threading import Event, Thread
from FlamingoConnect import FlamingoConnect
# then use mc.etc as function calls
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


class Py2FlamingoGUI(QMainWindow):
    def __init__(self, queues_and_events):
        super().__init__()
        self.microscope_connection = FlamingoConnect(queues_and_events)
        self.lasers = []
        self.start_position = ["", "", "", ""]
        self.selected_laser = ""
        self.laser_power = ""
        self.data_storage_location = ""
        self.current_coordinates = []

        self.setWindowTitle(f"Flamingo Controller: {self.microscope_connection.instrument_name}")
        self.setStyleSheet("QMainWindow { background-color: rgb(255, 230, 238); }")
        self.setGeometry(200, 200, 600, 550)

        # Create labels and fields
        self.create_image_label()  # Create the QLabel widget for displaying the image
        self.create_GUI_layout()
        # self.create_buttons()
        self.update_display_timer()

    def create_image_label(self):
        self.image_label = QLabel(self)  # Create the QLabel widget
        self.image_label.setAlignment(
            Qt.AlignCenter
        )  # Center-align the image within the label

        # self.image_label.setGeometry(400, 1000, 512, 512)  # Set the position and size of the label within the main window

    def update_display_timer(self):
        self.display_timer = QTimer()
        self.display_timer.timeout.connect(self.update_display)

        # Set the interval for the QTimer (e.g., update every 100 milliseconds)
        update_interval = 200
        self.display_timer.start(update_interval)

    def update_display(self):
        # Check if there are images in the queue
        if not visualize_queue.empty():
            if visualize_event.is_set():
                visualize_event.clear()
                # Get the latest image from the queue
                while visualize_queue.qsize() > 1:
                    visualize_queue.get()
                image = visualize_queue.get()
                self.display_image(image)
                #print(f"image to display is {image.shape} and {image.dtype}")
                # Update the display with the image
                if not stage_location_queue.empty():
                    # in case data transfer was slow, get the last shown image location only.
                    while stage_location_queue.qsize() > 1:
                        stage_location_queue.get()
                    self.current_coordinates = stage_location_queue.get()

                    self.current_x.setText(
                        f"x(mm): {round(float(self.current_coordinates[0]),2)}"
                    )
                    self.current_y.setText(
                        f"y(mm): {round(float(self.current_coordinates[1]),2)}"
                    )
                    self.current_z.setText(
                        f"z(mm): {round(float(self.current_coordinates[2]),2)}"
                    )
                    self.current_r.setText(
                        f"r(°): {round(float(self.current_coordinates[3]),2)}"
                    )
            else:
                image = visualize_queue.get()

    def display_image(self, image):
        # print('display_image')
        # Convert the numpy array image to QImage
        q_image = convert_to_qimage(image)

        # Create a pixmap and set it as the label's pixmap
        pixmap = QPixmap.fromImage(q_image)
        pixmap = pixmap.scaled(QSize(512, 512), Qt.KeepAspectRatio)
        self.image_label.setPixmap(pixmap)


    def create_GUI_layout(self):
        global_layout = QHBoxLayout()  # Create a QHBoxLayout for the main layout
        start_position_widget = QWidget()
        # Create a QHBoxLayout for the tip of the sample holder
        start_position_form = (
            QHBoxLayout(start_position_widget)
        )  

        vertical_layout = (
            QVBoxLayout()
        )  # Create a QVBoxLayout to combine the radio buttons, form fields, and buttons
        #start_position_widget.setStyleSheet("background-color: rgb(255, 213, 168);")
        start_position_widget.setStyleSheet(
            """
            QLineEdit {
                background-color: beige;
            }
            QLineEdit:focus {
                background-color: yellow;
            }
            """
        )
        start_position_widget.setToolTip(
            "The provided coordinates are used for the Find Sample button"
        )
        position_widget = QWidget()
        position_form = (
            QHBoxLayout(position_widget)
        )  # Create a QHBoxLayout for the active positioning and snapshots
        position_widget.setStyleSheet(
            """
            QLineEdit {
                background-color: paleturquoise;
            }
            QLineEdit:focus {
                background-color: yellow;
            }
            """
        )

        # Create form fields for sample start location
        self.start_label_x_mm = QLabel("x (mm): ")
        self.start_field_x_mm = self.create_field(default_value=self.microscope_connection.start_position[0])

        self.start_label_y_mm = QLabel("y (mm): ")
        self.start_field_y_mm = self.create_field(default_value=self.microscope_connection.start_position[1])

        self.start_label_z_mm = QLabel("z (mm): ")
        self.start_field_z_mm = self.create_field(default_value=self.microscope_connection.start_position[2])

        self.start_label_r_deg = QLabel("r (°): ")
        self.start_field_r_deg = self.create_field(default_value=self.microscope_connection.start_position[3])

        # Set current coordinates
        self.current_coordinates = [
            self.microscope_connection.start_position[0],
            self.microscope_connection.start_position[1],
            self.microscope_connection.start_position[2],
            self.microscope_connection.start_position[3],
        ]

        # Add form fields and labels to the layout
        start_position_form.addWidget(self.start_label_x_mm)
        start_position_form.addWidget(self.start_field_x_mm)
        start_position_form.addWidget(self.start_label_y_mm)
        start_position_form.addWidget(self.start_field_y_mm)
        start_position_form.addWidget(self.start_label_z_mm)
        start_position_form.addWidget(self.start_field_z_mm)
        start_position_form.addWidget(self.start_label_r_deg)
        start_position_form.addWidget(self.start_field_r_deg) 
        # Set current coordinates
        self.current_coordinates = [
            self.start_position[0],
            self.start_position[1],
            self.start_position[2],
            self.start_position[3],
        ]
        # form_layout.addRow('Initial XYZR coordinate for 'Find Sample'. Also Snapshot position.')

        # Create form fields to get location for actions from user
        self.label_x_mm = QLabel("x (mm): ")
        self.field_x_mm = self.create_field(default_value=self.start_position[0])

        self.label_y_mm = QLabel("y (mm): ")
        self.field_y_mm = self.create_field(default_value=self.start_position[1])

        self.label_z_mm = QLabel("z (mm): ")
        self.field_z_mm = self.create_field(default_value=self.start_position[2])

        self.label_r_deg = QLabel("r (°): ")
        self.field_r_deg = self.create_field(default_value=self.start_position[3])



        # Add form fields and labels to the layout
        position_form.addWidget(self.label_x_mm)
        position_form.addWidget(self.field_x_mm)
        position_form.addWidget(self.label_y_mm)
        position_form.addWidget(self.field_y_mm)
        position_form.addWidget(self.label_z_mm)
        position_form.addWidget(self.field_z_mm)
        position_form.addWidget(self.label_r_deg)
        position_form.addWidget(self.field_r_deg)

        vertical_layout.setAlignment(Qt.AlignTop)
        self.create_radio_buttons(vertical_layout)
        self.create_form_fields(vertical_layout)
        vertical_layout.addWidget(start_position_widget)
        vertical_layout.addWidget(position_widget)
        self.create_buttons(vertical_layout)

        # Add the combined vertical layouts on the left and the image layout on the right to the horizontal layout
        global_layout.addLayout(vertical_layout)
        # Create a QVBoxLayout for the image plus coordinates
        image_layout = (
            QVBoxLayout()
        )  
        self.fill_image_layout(image_layout)
        global_layout.addLayout(image_layout)
        central_widget = QWidget()  # Create a QWidget for the central widget
        central_widget.setLayout(global_layout)  # Set the layout for the central widget
        self.setCentralWidget(
            central_widget
        )  # Set the central widget for the main window

    def create_form_fields(self, layout):
        # Create a QHBoxLayout for the form fields
        form_layout = QFormLayout()
        # Create the form fields with their default values
        self.create_form_field(form_layout, "IP Address", self.microscope_connection.IP, is_string=True)
        self.create_form_field(form_layout, "command port (default 53717)", self.microscope_connection.port)
        self.create_form_field(form_layout, "Laser Power (percent):", self.microscope_connection.laser_power, is_string=True)
        self.create_form_field(form_layout, "Z Search Depth (mm):", "2.0")
        self.create_form_field(form_layout, "Data storage path:", self.microscope_connection.data_storage_location, is_string=True)
        layout.addLayout(form_layout)

    def create_form_field(self, layout, label, default_value, is_string=False):
        field = self.create_field(is_string=is_string, default_value=default_value)
        var_name = ''.join(e for e in label if e.isalnum() or e.isspace()).replace(" ", "_").lower()[:12].rstrip('_')
        #print(var_name)
        setattr(self, var_name, field)
        layout.addRow(f"{self.microscope_connection.instrument_name} {label}", field)




    def create_buttons(self, layout):
        button_layout1 = QVBoxLayout()  # Create the first QVBoxLayout for buttons
        button_layout2 = QVBoxLayout()  # Create the second QVBoxLayout for buttons
        button_layout = QHBoxLayout()   # Create a QHBoxLayout to hold the two columns
        kill_button_style = """
            QPushButton:hover {
                background-color: yellow;
            }
            QPushButton {
                border: 2px solid red;
                border-radius: 5px;
                padding: 5px;
                background-color: white;
            }
            QPushButton:hover {
                border: 2px solid red;
            }
            QPushButton:pressed {
                border: 2px solid white;
            }
        """
        coordinate_button_style = """
            QPushButton:hover {
                background-color: yellow;
            }
            QPushButton {
                background-color: paleturquoise;
            }
            QPushButton:hover {
                border: 2px solid red;
            }
            QPushButton:pressed {
                border: 2px solid white;
            }
        """
        find_focus_button_style = """
            QPushButton:hover {
                background-color: yellow;
            }
            QPushButton {
                background-color: beige;
            }
            QPushButton:hover {
                border: 2px solid red;
            }
            QPushButton:pressed {
                border: 2px solid white;
            }
        """
        # Create and connect the buttons
        self.create_button(button_layout1, "Find Sample", self.locate_sample_action, "245, 245, 220",find_focus_button_style)
        self.create_button(button_layout2, "Go to XYZR", self.go_to_position, "175, 238, 238", coordinate_button_style)
        self.create_button(button_layout2, "Take IF snapshot", self.take_snapshot, "175, 238, 238", coordinate_button_style)
        self.create_button(button_layout2, "Copy position of image", self.copy_current_position, "175, 238, 238", coordinate_button_style)
        self.create_button(button_layout2, "Set 'Home' on Mac", self.set_home_action, "175, 238, 238", coordinate_button_style)
        self.create_button(button_layout1, "Duplicate and add your button", self.add_your_code)
        self.create_button(button_layout1, "Stop AND CLOSE PROGRAM", self.close_program, "255, 255, 255", kill_button_style)
        self.create_button(button_layout1, "Cancel current process", self.cancel_action, "255, 255, 255", kill_button_style)
        # Add the combined vertical layouts (radio buttons, form fields, and buttons) to the main vertical layout
        button_layout.addLayout(button_layout1)
        button_layout.addLayout(button_layout2)
        layout.addLayout(button_layout)

    def create_button(self, layout, label, action, color=None, style=None):
        button = QPushButton(label, self)
        button.clicked.connect(action)
        if color:
            button.setStyleSheet(f"background-color: rgb({color});")
        if style:
            button.setStyleSheet(style)
        layout.addWidget(button)


    def fill_image_layout(self, layout):
        layout.setAlignment(Qt.AlignTop)
        layout.addWidget(self.image_label)  # Add the image label to the image layout
        # initialize current position values with the starting position
        self.current_x = QLabel()
        self.current_y = QLabel()
        self.current_z = QLabel()
        self.current_r = QLabel()
        current_position_layout = QHBoxLayout()  # Create a QHBoxLayout for the current position
        current_position_layout.addWidget(self.current_x)
        current_position_layout.addWidget(self.current_y)
        current_position_layout.addWidget(self.current_z)
        current_position_layout.addWidget(self.current_r)
        layout.addLayout(current_position_layout)

    def create_radio_buttons(self, layout):
        radio_layout = QHBoxLayout()  # Create a QHBoxLayout for radio buttons
        # Get labels from the parsed metadata file
        labels = self.microscope_connection.lasers
        self.radio_buttons = []
        for label in labels:
            radio_button = QRadioButton(label)  # Create a QRadioButton for each label
            if label == self.microscope_connection.selected_laser:
                radio_button.setChecked(True)  # Set the checked state based on the selected laser
            self.radio_buttons.append(radio_button)  # Add the radio button to the list
            radio_layout.addWidget(radio_button)  # Add the radio button to the radio layout
        layout.addLayout(radio_layout)
    def create_field(self, default_value=None, is_string=False):
        # Create a QLineEdit widget
        field = QLineEdit(self)

        # Align the text to the right within the QLineEdit
        field.setAlignment(Qt.AlignRight)

        if default_value is not None:
            if not is_string:
                # Set a validator to restrict the input to valid floating-point numbers
                field.setValidator(QDoubleValidator())
                default_value = str(default_value)  # Convert float to string
            field.setText(default_value)
        return field

    def check_field(self, entries):
        for entry in entries:
            text = entry.text().strip()

            # Check if the field is empty
            if text == "":
                message_box = QMessageBox()
                message_box.setIcon(QMessageBox.Warning)
                message_box.setWindowTitle("Field error")
                message_box.setText(
                    "A necessary field to perform this function is blank!"
                )
                message_box.setStandardButtons(QMessageBox.Ok)
                message_box.exec_()
                return False

            # Attempt to convert the text to a float
            try:
                value = float(text)
                #print("Value:", value)
            except ValueError:
                message_box = QMessageBox()
                message_box.setIcon(QMessageBox.Warning)
                message_box.setWindowTitle("Settings required")
                message_box.setText(
                    "To find the sample requires a starting point (XYZR), generally near the tip of the sample holder. One of the fields does not contain a numerical value."
                )
                message_box.setStandardButtons(QMessageBox.Ok)
                message_box.exec_()
                # QMessageBox.warning(central_widget, 'Field Error', 'Invalid number!')
                return False
        return True

    def go_to_position(self):
        if not self.check_field(
            [self.field_x_mm, self.field_y_mm, self.field_z_mm, self.field_r_deg]
        ):
            return
        # Get values from the GUI fields

        xyzr_init = [
            self.field_x_mm.text(),
            self.field_y_mm.text(),
            self.field_z_mm.text(),
            self.field_r_deg.text(),
        ]
        # Create a thread for the locate_sample function
        go_to_position_thread = Thread(
            target=go_to_position,
            args=(xyzr_init, command_data_queue, command_queue, send_event),
        )

        # Set the thread as a daemon (exits when the main program exits)
        go_to_position_thread.daemon = True

        # Start the thread
        go_to_position_thread.start()

    def locate_sample_action(self):
        # Check for a start position

        if not self.check_field(
            [self.start_field_x_mm, self.start_field_y_mm, self.start_field_z_mm, self.start_field_r_deg]
        ):
            return
        # Get values from the GUI fields
        print(self.laser_power.text())
        print(self.z_search_dep.text())
        print(self.data_storage.text())
        laser_value = self.get_selected_radio_value()
        power_percent_value = self.laser_power.text()
        z_depth_value = self.z_search_dep.text()
        data_storage = self.data_storage.text()
        updated_position = {
            self.microscope_connection.instrument_name: {
                "x(mm)": self.start_field_x_mm.text(),
                "y(mm)": self.start_field_y_mm.text(),
                "z(mm)": self.start_field_z_mm.text(),
                "r(°)": self.start_field_r_deg.text(),
            }
        }
        xyzr_init = [
            self.start_field_x_mm.text(),
            self.start_field_y_mm.text(),
            self.start_field_z_mm.text(),
            self.start_field_r_deg.text(),
        ]
        print(xyzr_init)
        dict_to_text(
            os.path.join(
                "microscope_settings", f"{self.microscope_connection.instrument_name}_start_position.txt"
            ),
            updated_position,
        )
        alist = [                self.microscope_connection.connection_data,
                xyzr_init,
                visualize_event,
                other_data_queue,
                image_queue,
                command_queue,
                z_plane_queue,
                intensity_queue,
                system_idle,
                processing_event,
                send_event,
                terminate_event,
                command_data_queue,
                stage_location_queue,
                laser_value,
                power_percent_value,
                z_depth_value,
                data_storage]
        # print('print check')
        # for item in alist:
        #     print(item)
        # Create a thread for the locate_sample function
        locate_sample_thread = Thread(
            target=locate_sample,
            args=(
                self.microscope_connection.connection_data,
                xyzr_init,
                visualize_event,
                other_data_queue,
                image_queue,
                command_queue,
                z_plane_queue,
                intensity_queue,
                system_idle,
                processing_event,
                send_event,
                terminate_event,
                command_data_queue,
                stage_location_queue,
                laser_value,
                power_percent_value,
                z_depth_value,
                data_storage,
            ),
        )

        # Set the thread as a daemon (exits when the main program exits)
        locate_sample_thread.daemon = True

        # Start the thread
        locate_sample_thread.start()

    def get_selected_radio_value(self):
        for radio_button in self.radio_buttons:
            if radio_button.isChecked():
                return radio_button.text()

        return None

    def close_program(self):
        (
            live_listen_thread_var,
            command_listen_thread_var,
            send_thread_var,
            processing_thread_var,
        ) = self.microscope_connection.threads
        nuc_client, live_client, _, _, _ = self.microscope_connection.connection_data
        terminate_event.set()
        QApplication.instance().quit()
        print("Shutting down connection")
        close_thread = Thread(
            target=mc.close_connection,
            args=(
                nuc_client,
                live_client,
                live_listen_thread_var,
                command_listen_thread_var,
                send_thread_var,
                processing_thread_var,
            ),
            group=None,
        )
        close_thread.daemon = True
        close_thread.start()

        self.close()

    def take_snapshot(self):
        # Open the PNG file
        if not self.check_field(
            [self.field_x_mm, self.field_y_mm, self.field_z_mm, self.field_r_deg]
        ):
            return
        print('snapshot check passed')
        xyzr = [
            self.field_x_mm.text(),
            self.field_y_mm.text(),
            self.field_z_mm.text(),
            self.field_r_deg.text(),
        ]
        #print(f'snapshot coordinates {xyzr}')
        laser_value = self.get_selected_radio_value()
        power_percent_value = self.laser_power.text()
        # all functions must be run on a separate thread, targetting a function, including its arguments, and then start()
        take_snapshot_thread = Thread(
            target=take_snapshot,
            args=(
                self.microscope_connection.connection_data,
                xyzr,
                visualize_event,
                image_queue,
                command_queue,
                stage_location_queue,
                send_event,
                laser_value,
                power_percent_value,
            ),
        )
        # Set the thread as a daemon (exits when the main program exits)
        take_snapshot_thread.daemon = True
        # Start the thread
        take_snapshot_thread.start()

    def set_home_action(self):

        # set the Home coordinates to the currently displayed xyzr under the image

        if not self.check_field(
            [self.field_x_mm, self.field_y_mm, self.field_z_mm, self.field_r_deg]
        ):
            return
        xyzr_init = [
            float(self.current_x.text().split(" ")[1]),
            float(self.current_y.text().split(" ")[1]),
            float(self.current_z.text().split(" ")[1]),
            float(self.current_r.text().split(" ")[1]),
        ]
        print(f"New 'Home' coordinates {xyzr_init}")

        # all functions must be run on a separate thread, targetting a function, including its arguments, and then start()
        set_home_thread = Thread(
            target=set_home,
            args=(
                self.microscope_connection.connection_data,
                xyzr_init,
                command_queue,
                other_data_queue,
                send_event,
            ),
        )
        # Set the thread as a daemon (exits when the main program exits)
        set_home_thread.daemon = True
        # Start the thread
        set_home_thread.start()

    def copy_current_position(self):
        """
        Copy to the cyan colored active fields either
        1. The position of the microscope after some function, as shown under the GUI image
        2. The initial positions
        otherwise ignore.
        """
        if not all([self.current_x.text(), self.current_y.text(), self.current_z.text(), self.current_r.text()]):
            if self.check_field(
            [self.start_field_x_mm, self.start_field_y_mm, self.start_field_z_mm, self.start_field_r_deg]
                ):
                self.field_x_mm.setText(str(self.start_field_x_mm.text()))
                self.field_y_mm.setText(str(self.start_field_y_mm.text()))
                self.field_z_mm.setText(str(self.start_field_z_mm.text()))
                self.field_r_deg.setText(str(self.start_field_r_deg.text()))
                return
            else: return
            
        print(str(self.current_x.text()))
        self.field_x_mm.setText(str(self.current_x.text().split(" ")[1]))
        self.field_y_mm.setText(str(self.current_y.text().split(" ")[1]))
        self.field_z_mm.setText(str(self.current_z.text().split(" ")[1]))
        self.field_r_deg.setText(str(self.current_r.text().split(" ")[1]))

    def cancel_action(self):
        terminate_event.set()
        

    def add_your_code(self):
        # all functions must be run on a separate thread, targetting a function, including its arguments, and then start()
        pass
