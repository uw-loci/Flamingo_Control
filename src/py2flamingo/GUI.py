# TODO create text feedback box at bottom of GUI. Needs function to check for updated information in a Queue
# TODO replace default value with a calculated default based on Zmin and max from settings (form_layout, "Z Search Depth (mm):", "1.2")
# TODO prevent double clicking a button from killing the program, maybe.

import os
from threading import Thread

import functions.calculations as calc
import functions.microscope_connect as mc
import functions.microscope_interactions as scope
import functions.text_file_parsing as txt
from FlamingoConnect import FlamingoConnect, show_warning_message
from functions.image_display import convert_to_qimage
from functions.run_workflow_basic import run_workflow
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
from go_to_position import go_to_position
from locate_sample import locate_sample
from multi_angle_collection import multi_angle_collection
from PyQt5.QtCore import QSize, Qt, QTimer
from PyQt5.QtGui import QDoubleValidator, QIntValidator, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
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
from trace_ellipse import trace_ellipse


class CoordinateDialog(QDialog):
    def __init__(self, start_position, z_default, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Coordinate Dialog")
        self.setModal(True)

        # Create the layout and add the fields and buttons
        layout = QGridLayout()
        fields_layout = QHBoxLayout()
        sample_layout = QFormLayout()
        buttons_layout = QHBoxLayout()

        # Create the coordinate fields
        self.field_x_mm = QLineEdit()
        self.field_y_mm = QLineEdit()
        self.field_z_mm = QLineEdit()
        self.field_r_deg = QLineEdit()
        self.sample_count = QLineEdit()
        self.z_search_depth = QLineEdit()

        # Set the initial values from the start_position
        self.field_x_mm.setText(str(start_position[0]))
        self.field_y_mm.setText(str(start_position[1]))
        self.field_z_mm.setText(str(start_position[2]))
        self.field_r_deg.setText(str(start_position[3]))
        self.sample_count.setText("1")
        self.z_search_depth.setText(str(z_default))

        # Set the validator for the sample_count field to allow only integer entries
        int_validator = QIntValidator()
        double_validator = QDoubleValidator()
        self.z_search_depth.setValidator(double_validator)
        self.sample_count.setValidator(int_validator)
        self.field_x_mm.setValidator(double_validator)
        self.field_y_mm.setValidator(double_validator)
        self.field_z_mm.setValidator(double_validator)
        self.field_r_deg.setValidator(double_validator)

        # Create the Okay and Cancel buttons
        self.button_okay = QPushButton("Okay")
        self.button_cancel = QPushButton("Cancel")

        # Connect the "Cancel" button's signal to reject the dialog
        self.button_cancel.clicked.connect(self.reject)

        # Set the help text
        self.setToolTip(
            "Enter start coordinates for the search. These should ideally be near the tip of the sample holder.\n Expected number of samples defaults to 1, but 0 will search the entire range. \nOther numbers (rounded to ints) will cause the search to end early when the indicated number of samples are found."
        )

        sample_layout.addRow(QLabel("Sample Count:"), self.sample_count)
        sample_layout.addRow(QLabel("Z search Depth (mm):"), self.z_search_depth)

        fields_layout.addWidget(QLabel("x (mm):"))
        fields_layout.addWidget(self.field_x_mm)
        fields_layout.addWidget(QLabel("y (mm):"))
        fields_layout.addWidget(self.field_y_mm)
        fields_layout.addWidget(QLabel("z (mm):"))
        fields_layout.addWidget(self.field_z_mm)
        fields_layout.addWidget(QLabel("r (°):"))
        fields_layout.addWidget(self.field_r_deg)

        buttons_layout.addWidget(self.button_okay)
        buttons_layout.addWidget(self.button_cancel)

        layout.addLayout(fields_layout, 0, 0, 1, 2)
        layout.addLayout(sample_layout, 1, 0)
        layout.addLayout(buttons_layout, 2, 0, 1, 2)
        layout.setColumnStretch(1, 1)  # Set the stretch factor for the second column

        # Set the layout for the dialog
        self.setLayout(layout)


class MultiAngleDialog(QDialog):
    def __init__(self, sample_name, parent=None):
        super().__init__(parent)

        # Set the window title
        self.setWindowTitle("Multi-angle data collection")
        self.setModal(True)

        # Create the Okay and Cancel buttons
        self.button_okay = QPushButton("Okay")
        self.button_cancel = QPushButton("Cancel")

        # Connect the "Cancel" button's signal to reject the dialog
        self.button_cancel.clicked.connect(self.reject)

        # Create the layout and add the fields and buttons
        layout = QGridLayout()
        layout.setColumnMinimumWidth(
            1, 200
        )  # Set the minimum width of the second column to 200
        # Create a QLabel with the note
        note_label = QLabel(
            """NOTE: This function does not use the values in the original dialog for microscope settings, \n
                            they are all expected to be in the workflow file that is selected. This includes the base file storage location! 
                            \nDue to the large amounts of data this function can generate, it is handled separately from the rest.
                            \n This function will not work as written for angle increments below one degree."""
        )

        # Set the font size to be larger
        note_label.setStyleSheet("font-size: 20px;")

        # Add the note label to the grid layout at the top
        layout.addWidget(note_label, 0, 0, 1, 3)

        # Create new fields
        self.field_increment_angle = QLineEdit("20")
        self.field_workflow_source_button = QPushButton("Browse")
        self.field_workflow_source = QLineEdit()
        self.field_sample_name = QLineEdit(sample_name)
        self.field_sample_comment = QLineEdit("TestComment")
        width = 500
        self.field_increment_angle.setMaximumWidth(width)
        self.field_workflow_source.setMaximumWidth(width)
        self.field_sample_name.setMaximumWidth(width)
        self.field_sample_comment.setMaximumWidth(width)
        # Set validators
        self.field_increment_angle.setValidator(QDoubleValidator())

        # Initialize workflow_source_path
        self.workflow_source_path = "workflows/"  # Default path

        # Connect browse button clicked signal to slot
        self.field_workflow_source_button.clicked.connect(self.browse_file)

        # Add new fields to the layout
        layout.addWidget(QLabel("Increment Angle (°):"), 1, 0)
        layout.addWidget(self.field_increment_angle, 1, 1)
        layout.addWidget(QLabel("Workflow Source:"), 2, 0)
        layout.addWidget(self.field_workflow_source, 2, 1)
        layout.addWidget(self.field_workflow_source_button, 2, 2)
        layout.addWidget(QLabel("Name for current run:"), 3, 0)
        layout.addWidget(self.field_sample_name, 3, 1)
        layout.addWidget(QLabel("Comment:"), 4, 0)
        layout.addWidget(self.field_sample_comment, 4, 1)

        # Add the Okay and Cancel buttons
        layout.addWidget(self.button_okay, 5, 0)
        layout.addWidget(self.button_cancel, 5, 1)

        # Set the layout for the dialog
        self.setLayout(layout)

        # Update the help text
        self.setToolTip(
            "Use an existing workflow with correct lasers, laser power, and collection settings to collect the sample at set angle increments based on an initial location."
        )

    def browse_file(self):
        """
        Slot for handling browse button clicked signal.
        Opens a QFileDialog and stores the selected file path.
        """
        options = QFileDialog.Options()
        options |= QFileDialog.DontUseNativeDialog
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "QFileDialog.getOpenFileName()",
            self.workflow_source_path,
            "All Files (*);;Text Files (*.txt)",
            options=options,
        )
        if file_name:
            self.workflow_source_path = file_name
            file_name_only = os.path.basename(
                file_name
            )  # Get only the file's name, excluding the path
            self.field_workflow_source.setText(
                file_name_only
            )  # Set the QLineEdit text to the selected file's name


class Py2FlamingoGUI(QWidget):
    """
    This class represents the main window of the Py2Flamingo application. It inherits from QMainWindow, a class in PyQt5 that provides a main application window.

    The Py2FlamingoGUI class is responsible for initializing the connection to the microscope through FlamingoConnect, creating the GUI layout, and updating the display.

    Attributes
    ----------
    microscope_connection : FlamingoConnect
        The connection to the microscope, which is established through FlamingoConnect.
    lasers : list
        A list of lasers available for use with the microscope.
    start_position : list
        The starting position of the microscope, represented as a list of four strings.
    selected_laser : str
        The laser currently selected for use.
    laser_power : str
        The power level of the laser in percent.
    data_storage_location : str
        The location where data is saved, generally a USB drive connected to the microscope controller.
    current_coordinates : list
        The current coordinates of the microscope, represented as a list of four strings.
    """

    def __init__(self, queues_and_events):
        super().__init__()
        # Add a class attribute to keep track of created threads
        # Goal, do not have multiple threads active targeting the microscope at once, like finding the sample and collecting data or going to a position.
        self.threads = []
        # Initialize the connection to the microscope
        self.microscope_connection = FlamingoConnect(queues_and_events)
        self.lasers = []
        self.start_position = ["", "", "", ""]
        self.selected_laser = ""
        self.laser_power = ""
        self.data_storage_location = ""
        self.current_coordinates = []
        # Set the window title and style
        self.setWindowTitle(
            f"Flamingo Controller: {self.microscope_connection.instrument_name}"
        )
        self.setStyleSheet("QMainWindow { background-color: rgb(255, 230, 238); }")
        self.setGeometry(200, 200, 600, 550)

        # Create labels and fields
        self.create_image_label()  # Create the QLabel widget for displaying the image
        self.create_GUI_layout()  # Create the layout of the GUI
        self.update_display_timer()  # Start the timer for updating the display

    def create_GUI_layout(self):
        """
        This function creates the main layout for the Py2Flamingo GUI.

        The GUI is divided into two main sections: a left interface and an image display on the right. The left interface
        contains radio buttons for selecting the laser, form fields for entering various parameters, two sets of coordinate
        fields (one for the start position and one for the active position), and several buttons for performing actions like
        locating the sample, going to a specific position, and taking a snapshot. The image display shows the current image
        from the microscope and the current coordinates.

        The layout is created using Qt's QHBoxLayout and QVBoxLayout classes. The QHBoxLayout is used for the main layout,
        which contains the left interface layout and the image layout. The QVBoxLayout is used for the left interface layout,
        which contains the radio buttons, form fields, coordinate fields, and buttons.

        The function also sets the current coordinates to the start position obtained from the microscope connection.

        Parameters
        ----------
        None

        Returns
        -------
        None
        """

        # Create a QHBoxLayout for the main layout
        global_layout = QHBoxLayout()

        # Create a QVBoxLayout for the left interface layout
        left_interface_layout = QVBoxLayout()

        # Set the current coordinates to the start position from the microscope connection
        self.current_coordinates = [
            self.microscope_connection.start_position[0],
            self.microscope_connection.start_position[1],
            self.microscope_connection.start_position[2],
            self.microscope_connection.start_position[3],
        ]

        # Align the left interface layout to the top
        left_interface_layout.setAlignment(Qt.AlignTop)

        # Create the radio buttons, form fields, and coordinate fields, and add them to the left interface layout
        self.create_radio_buttons(left_interface_layout)
        self.create_form_fields(left_interface_layout)
        self.create_coordinate_fields(
            left_interface_layout,
            "",
            self.microscope_connection.start_position,
            "paleturquoise",
        )

        # Create the buttons and add them to the left interface layout
        self.create_buttons(left_interface_layout)

        # Add the left interface layout and the image layout to the main layout
        global_layout.addLayout(left_interface_layout)
        image_layout = QVBoxLayout()  # Create a QVBoxLayout for the image layout
        self.fill_image_layout(image_layout)
        global_layout.addLayout(image_layout)

        # Create a QWidget for the central widget, set its layout to the main layout, and set it as the central widget for the main window
        central_widget = QWidget()
        central_widget.setLayout(global_layout)
        self.setCentralWidget(central_widget)

    def create_image_label(self):
        """
        This function creates a QLabel widget to display the microscope image. The image is center-aligned within the label.

        Returns
        -------
        None
        """
        self.image_label = QLabel(self)  # Create the QLabel widget
        self.image_label.setAlignment(
            Qt.AlignCenter
        )  # Center-align the image within the label

    def update_display_timer(self):
        """
        This function creates a QTimer to periodically update the display with the latest microscope image.

        Returns
        -------
        None
        """
        self.display_timer = QTimer()  # Create the QTimer
        self.display_timer.timeout.connect(
            self.update_display
        )  # Connect the QTimer timeout signal to the update_display function

        update_interval = (
            200  # Set the interval for the QTimer (e.g., update every 200 milliseconds)
        )
        self.display_timer.start(update_interval)  # Start the QTimer

    def update_display(self):  # sourcery skip: extract-method, last-if-guard
        """
        This function updates the display with the latest microscope image and the current coordinates. If there are multiple
        images in the queue, it discards all but the latest one.

        Returns
        -------
        None
        """
        # Check if there are images in the queue
        if not visualize_queue.empty():
            if visualize_event.is_set():
                visualize_event.clear()
                # Get the latest image from the queue
                while visualize_queue.qsize() > 1:
                    visualize_queue.get()  # Discard all but the latest image
                image = visualize_queue.get()  # Get the latest image
                self.display_image(image)  # Display the image

                # Update the display with the current coordinates
                if not stage_location_queue.empty():
                    # In case data transfer was slow, get the last shown image location only.
                    while stage_location_queue.qsize() > 1:
                        stage_location_queue.get()  # Discard all but the latest coordinates
                    self.current_coordinates = (
                        stage_location_queue.get()
                    )  # Get the latest coordinates

                    # Update the coordinate labels with the current coordinates
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
                image = (
                    visualize_queue.get()
                )  # Get the latest image without updating the coordinates

    def display_image(self, image):
        """
        This function displays the given image on the QLabel widget created by the create_image_label function.
        It first converts the numpy array image to a QImage, then creates a QPixmap from the QImage, scales it to fit
        the QLabel, and finally sets it as the QLabel's pixmap.

        Parameters
        ----------
        image : numpy.ndarray
            The image to be displayed, represented as a numpy array.

        Returns
        -------
        None
        """
        # Convert the numpy array image to QImage
        q_image = convert_to_qimage(image)

        # Create a QPixmap from the QImage
        pixmap = QPixmap.fromImage(q_image)

        # Scale the QPixmap to fit the QLabel while maintaining the aspect ratio
        pixmap = pixmap.scaled(QSize(512, 512), Qt.KeepAspectRatio)

        # Set the QPixmap as the QLabel's pixmap
        self.image_label.setPixmap(pixmap)

    def create_form_fields(self, layout):
        """
        This function creates the form fields for the GUI and adds them to the provided layout.

        The form fields include the IP address, command port, laser power, Z search depth, and data storage path. Each form
        field is created using the create_form_field function, which also adds the form field to a QFormLayout. The QFormLayout
        is then added to the provided layout.

        Parameters
        ----------
        layout : QVBoxLayout
            The layout to which the form fields are added.

        Returns
        -------
        None
        """

        # Create a QFormLayout for the form fields
        form_layout = QFormLayout()

        # Create the form fields with their default values and add them to the form layout
        self.create_form_field(
            form_layout,
            "IP Address",
            self.microscope_connection.IP,
            is_string=True,
            read_only=True,
        )
        self.create_form_field(
            form_layout,
            "command port (default 53717)",
            self.microscope_connection.port,
            read_only=True,
        )
        self.create_form_field(
            form_layout,
            "Laser Power (percent):",
            self.microscope_connection.laser_power,
            is_string=True,
        )
        self.create_form_field(
            form_layout,
            "Data storage path:",
            self.microscope_connection.data_storage_location,
            is_string=True,
        )
        self.create_form_field(
            form_layout, "Sample Name", "Default", is_string=True,
        )

        # Add the form layout to the provided layout
        layout.addLayout(form_layout)

    def create_form_field(
        self, layout, label, default_value, is_string=False, read_only=False
    ):
        """
        This function creates a form field with the provided label and default value, and adds it to the provided layout.

        The function also sets an attribute of the GUI object with the name derived from the label and the value of the form field.
        This allows the form field to be accessed elsewhere in the program using the attribute.

        Parameters
        ----------
        layout : QFormLayout
            The layout to which the form field is added.
        label : str
            The label for the form field.
        default_value : str or float
            The default value for the form field.
        is_string : bool, optional
            Whether the form field should accept string input. If False (default), the form field will only accept floating-point numbers.
        read_only : bool, optional
            Whether the form field should be read-only. If True, the user cannot modify the form field's contents.

        Returns
        -------
        None
        """

        # Create a QLineEdit widget for the form field
        field = self.create_field(is_string=is_string, default_value=default_value)

        # If the read_only parameter is True, set the widget to be read-only
        if read_only:
            field.setReadOnly(True)

        # Generate a variable name from the label
        var_name = (
            "".join(e for e in label if e.isalnum() or e.isspace())
            .replace(" ", "_")
            .lower()[:12]
            .rstrip("_")
        )

        # Set an attribute of the GUI object with the variable name and the value of the form field
        setattr(self, var_name, field)

        # Add the form field to the provided layout
        layout.addRow(f"{label}", field)

    def create_coordinate_fields(self, layout, prefix, start_position, color):
        """
        This function creates the coordinate fields for the GUI and adds them to the provided layout.

        The coordinate fields include the x, y, z, and r coordinates. Each coordinate field is created using the
        create_coordinate_field function, which also adds the coordinate field to a QHBoxLayout. The QHBoxLayout is then
        added to the provided layout.

        Parameters
        ----------
        layout : QVBoxLayout
            The layout to which the coordinate fields are added.
        prefix : str
            The prefix for the coordinate field labels and variable names.
        start_position : list of float
            The default values for the coordinate fields.
        color : str
            The background color for the coordinate fields.

        Returns
        -------
        None
        """

        # Create a QWidget and a QHBoxLayout for the coordinate fields
        position_widget = QWidget()
        position_form = QHBoxLayout(position_widget)

        # Set the background color for the coordinate fields
        position_widget.setStyleSheet(
            f"""
            QLineEdit {{
                background-color: {color};
            }}
            QLineEdit:focus {{
                background-color: yellow;
            }}
            """
        )

        # Create the coordinate fields and add them to the QHBoxLayout
        coordinates = ["x", "y", "z", "r"]
        units = ["mm", "mm", "mm", "°"]
        if prefix:
            prefix = f"{prefix}_"
        var_names = [f"{prefix}field_{coord}_mm" for coord in coordinates]
        var_names[
            -1
        ] = f"{prefix}field_r_deg"  # The variable name for 'r' should end with '_deg', not '_mm'
        for coord, unit, var_name in zip(coordinates, units, var_names):
            self.create_coordinate_field(
                position_form,
                f"{prefix}{coord} ({unit}): ",
                start_position[coordinates.index(coord)],
                var_name,
            )

        # Add the QHBoxLayout to the provided layout
        layout.addWidget(position_widget)

    def create_coordinate_field(self, layout, label, default_value, var_name):
        """
        This function creates a coordinate field with the provided label and default value, and adds it to the provided layout.

        The function also sets an attribute of the GUI object with the name derived from the label and the value of the coordinate field.
        This allows the coordinate field to be accessed elsewhere in the program using the attribute.

        Parameters
        ----------
        layout : QHBoxLayout
            The layout to which the coordinate field is added.
        label : str
            The label for the coordinate field.
        default_value : float
            The default value for the coordinate field.
        var_name : str
            The variable name for the coordinate field.

        Returns
        -------
        None
        """

        # Create a QLineEdit widget for the coordinate field
        field = self.create_field(default_value=default_value)

        # Set an attribute of the GUI object with the variable name and the value of the coordinate field
        setattr(self, var_name, field)

        # Add the coordinate field to the provided layout
        layout.addWidget(QLabel(label))
        layout.addWidget(field)

    def create_buttons(self, layout):
        """
        This function creates a set of buttons for the GUI and adds them to the provided layout. Each button is associated with a specific action.
        Useful list of colors: https://www.w3.org/TR/SVG11/types.html#ColorKeywords
        Parameters
        ----------
        layout : QVBoxLayout
            The layout to which the buttons are added.

        Returns
        -------
        None
        """
        # Create QVBoxLayouts for the buttons
        button_layout1 = QVBoxLayout()
        button_layout2 = QVBoxLayout()
        button_layout = QHBoxLayout()  # Create a QHBoxLayout to hold the two columns

        # Define button styles
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
        copy_coordinates_button_style = """
            QPushButton:hover {
                background-color: yellow;
            }
            QPushButton {
                border: 2px solid cyan;
                border-radius: 5px;
                padding: 5px;
                background-color: lavender;
            }
            QPushButton:hover {
                border: 2px solid red;
            }
            QPushButton:pressed {
                border: 2px solid white;
            }
        """
        data_collection_style = """
            QPushButton:hover {
                background-color: yellow;
            }
            QPushButton {
                border: 2px solid white;
                border-radius: 5px;
                padding: 5px;
                background-color: bisque;
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
        self.create_button(
            button_layout1,
            "Find Sample",
            self.locate_sample_dialog,
            "245, 245, 220",
            find_focus_button_style,
        )
        self.create_button(
            button_layout2,
            "Go to XYZR",
            self.go_to_position,
            "175, 238, 238",
            coordinate_button_style,
        )
        self.create_button(
            button_layout2,
            "Take IF snapshot",
            self.take_snapshot,
            "175, 238, 238",
            coordinate_button_style,
        )
        self.create_button(
            button_layout2,
            "Image position to fields (cyan)",
            self.copy_current_position,
            "175, 238, 238",
            copy_coordinates_button_style,
        )
        self.create_button(
            button_layout2,
            "Set 'Home' on Mac",
            self.set_home_action,
            "175, 238, 238",
            coordinate_button_style,
        )
        self.create_button(
            button_layout2,
            "Track sample by angle",
            self.trace_ellipse_action,
            "249, 132, 229",
            data_collection_style,
            tooltip="The selected file bounding box is used to track the sample through a 360 degree rotation.\n The first position is assumed to be the provided point, and is assumed to be correct.\n Outputs are stored in 'sampletxt/Sample Name' folder",
        )
        self.create_button(
            button_layout2,
            "Find sample at angle",
            self.predict_sample_focus_at_angle,
            "175,238,238",
            coordinate_button_style,
            tooltip="Given the current 'Sample Name', the angle in the cyan user input box 'r' and the result of the ellipse calculation generated \nby the 'Track sample by angle' button predict the location of the sample at the given angle, and take a snapshot",
        )

        self.create_button(
            button_layout1,
            "Multi-angle collection",
            self.multi_angle_dialog,
            style=data_collection_style,
        )
        self.create_button(
            button_layout1,
            "Collect volume",
            self.basic_workflow_action,
            style=data_collection_style,
        )
        self.create_button(
            button_layout1, "Duplicate and add your button", self.add_your_code
        )
        self.create_button(
            button_layout1,
            "Stop AND CLOSE PROGRAM",
            self.close_program,
            "255, 255, 255",
            kill_button_style,
        )
        self.create_button(
            button_layout1,
            "Cancel current process",
            self.cancel_action,
            "255, 255, 255",
            kill_button_style,
        )
        # Add the combined vertical layouts (radio buttons, form fields, and buttons) to the main vertical layout
        button_layout.addLayout(button_layout1)
        button_layout.addLayout(button_layout2)
        layout.addLayout(button_layout)

    def create_button(
        self, layout, label, action, color=None, style=None, tooltip=None
    ):
        """
            This function creates a button with the provided label and action, and adds it to the provided layout.

            Parameters
            ----------
            layout : QVBoxLayout
                The layout to which the button is added.
            label : str
                The label for the button.
            action : function
                The function to be executed when the button is clicked.
            color : str, optional
                The background colorSure, here's the continuation of the `create_button` function's docstring and comments:

        ```python
                for the button. If provided, the button's background color is set to this color.
            style : str, optional
                The style for the button. If provided, the button's style is set to this style.

            Returns
            -------
            None
        """
        # Create a QPushButton widget for the button
        button = QPushButton(label, self)

        # Connect the button's clicked signal to the provided action
        button.clicked.connect(action)

        # Set the button's background color if a color is provided
        if color:
            button.setStyleSheet(f"background-color: rgb({color});")

        # Set the button's style if a style is provided
        if style:
            button.setStyleSheet(style)
        # Set the tooltip for the button if tooltip text is provided
        if tooltip:
            button.setToolTip(tooltip)
        # Add the button to the provided layout
        layout.addWidget(button)

    def fill_image_layout(self, layout):
        """
        This function adds an image label and current position labels to the provided layout.

        Parameters
        ----------
        layout : QVBoxLayout
            The layout to which the image label and current position labels are added.

        Returns
        -------
        None
        """
        # Align the layout's contents to the top
        layout.setAlignment(Qt.AlignTop)

        # Add the image label to the layout
        layout.addWidget(self.image_label)

        # Initialize current position labels with the starting position
        self.current_x = QLabel()
        self.current_y = QLabel()
        self.current_z = QLabel()
        self.current_r = QLabel()

        # Create a QHBoxLayout for the current position labels
        current_position_layout = QHBoxLayout()

        # Create a QWidget to apply color
        current_position_widget = QWidget()
        current_position_widget.setLayout(current_position_layout)
        current_position_widget.setStyleSheet("background-color: lavender;")

        # Add the current position labels to the QHBoxLayout
        current_position_layout.addWidget(self.current_x)
        current_position_layout.addWidget(self.current_y)
        current_position_layout.addWidget(self.current_z)
        current_position_layout.addWidget(self.current_r)

        # Add the QWidget to the provided layout
        layout.addWidget(current_position_widget)

    def create_radio_buttons(self, layout):
        """
        This function creates a set of radio buttons for the GUI and adds them to the provided layout. The labels for the radio buttons are obtained from the microscope connection.

        Parameters
        ----------
        layout : QVBoxLayout
            The layout to which the radio buttons are added.

        Returns
        -------
        None
        """
        # Create a QHBoxLayout for the radio buttons
        radio_layout = QHBoxLayout()

        # Get labels from the microscope connection
        labels = self.microscope_connection.lasers

        # Initialize a list to store the radio buttons
        self.radio_buttons = []

        # Create a QRadioButton for each label and add it to the radio layout
        for label in labels:
            radio_button = QRadioButton(label)
            if label == self.microscope_connection.selected_laser:
                radio_button.setChecked(
                    True
                )  # Set the checked state based on the selected laser
            self.radio_buttons.append(radio_button)  # Add the radio button to the list
            radio_layout.addWidget(
                radio_button
            )  # Add the radio button to the radio layout

        # Add the radio layout to the provided layout
        layout.addLayout(radio_layout)

    def create_field(self, default_value=None, is_string=False):
        """
        This function creates a QLineEdit widget with the provided default value.

        Parameters
        ----------
        default_value : str or float, optional
            The default value for the QLineEdit widget. If not provided, the QLineEdit widget is empty.
        is_string : bool, optional
            A flag indicating whether the default value is a string. If False, the QLineEdit widget is restricted to valid floating-point numbers.

        Returns
        -------
        QLineEdit
            The created QLineEdit widget.
        """
        # Create a QLineEdit widget
        field = QLineEdit(self)

        # Align the text to the right within the QLineEdit
        field.setAlignment(Qt.AlignRight)

        # If a default value is provided, set the QLineEdit widget's text to the default value
        if default_value is not None:
            # If the default value is not a string, restrict the QLineEdit widget's input to valid floating-point numbers
            if not is_string:
                field.setValidator(QDoubleValidator())
                default_value = str(
                    default_value
                )  # Convert the default value to a string

            # Set the QLineEdit widget's text to the default value
            field.setText(default_value)

        # Return the created QLineEdit widget
        return field

    def check_field(self, entries):
        """
        This function checks if the provided entries are valid. An entry is considered valid if it is not empty and can be converted to a float.

        Parameters
        ----------
        entries : list of QLineEdit
            The entries to check.

        Returns
        -------
        bool
            True if all entries are valid, False otherwise.
        """
        for entry in entries:
            text = entry.text().strip()

            # Check if the field is empty
            if text == "":
                return self.message_box(
                    "Field error",
                    "A necessary field to perform this function is blank!",
                )
            # Attempt to convert the text to a float
            try:
                value = float(text)
            except ValueError:
                return self.message_box(
                    "Settings required",
                    "To find the sample requires a starting point (XYZR), generally near the tip of the sample holder. \nOne of the fields does not contain a numerical value.",
                )
        # If all entries are valid, return True
        return True

    # TODO Rename this here and in `check_field`
    def message_box(self, title, message):
        # Display a warning message if the field is empty
        message_box = QMessageBox()
        message_box.setIcon(QMessageBox.Warning)
        message_box.setWindowTitle(title)
        message_box.setText(message)
        message_box.setStandardButtons(QMessageBox.Ok)
        message_box.exec_()
        return False

    def get_selected_radio_value(self):
        """
        This function returns the text of the selected radio button.

        Returns
        -------
        str
            The text of the selected radio button, or False if no radio button is selected.
        """
        for radio_button in self.radio_buttons:
            if radio_button.isChecked():
                return radio_button.text()
        # Display a warning message if the no radio option is selected
        self.message_box(
            self, "Radio button error", "A necessary radio button is not selected!"
        )
        return False

    def check_for_active_thread(self):
        # Check if any of the threads are still alive
        active_threads = [thread for thread in self.threads if thread.is_alive()]

        # Update the list of threads
        self.threads = active_threads
        # If there are any active threads, return True
        return bool(active_threads)

    def go_to_position(self):
        """
        This function starts a thread to go to the position specified in the GUI fields.

        Returns
        -------
        None
        """
        print("Go to position button pressed")
        # Make sure the thread is not already active if the function is attempted a second time
        if self.check_for_active_thread():
            print("Something else is still running!")
            return

        # Check if the fields for the position are valid
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

        # Create a thread for the go_to_position function
        self.go_to_position_thread = Thread(
            target=go_to_position,
            args=(xyzr_init, command_data_queue, command_queue, send_event),
        )

        # Set the thread as a daemon (exits when the main program exits)
        self.go_to_position_thread.daemon = True

        # Start the thread
        self.go_to_position_thread.start()
        self.threads.append(self.go_to_position_thread)

    def locate_sample_dialog(self):
        """
        This function initiates the process of locating the sample based on the values specified in the GUI fields.
        It checks the validity of the start position fields, retrieves values from the GUI fields, updates the position,
        and starts a thread for the locate_sample function.

        Returns
        -------
        None
        """
        print("Locate sample button pressed")
        # First ensure the laser is selected
        laser_value = self.get_selected_radio_value()
        if laser_value is False:
            return
        # Create the coordinate dialog
        dialog = CoordinateDialog(
            self.microscope_connection.start_position,
            self.microscope_connection.z_default,
            self,
        )

        # Connect the "Okay" button's signal to a lambda function that executes the remaining code
        dialog.button_okay.clicked.connect(lambda: self.locate_sample(dialog))

        # Show the dialog
        if dialog.exec() != QDialog.Accepted:
            # if Okay was not clicked, do not proceed with the function.
            return

    def locate_sample(self, dialog):
        # Make sure the thread is not already active if the function is attempted a second time
        if self.check_for_active_thread():
            print("Something else is still running!")
            return
        # Check if the fields for the start position are valid
        if not self.check_field(
            [
                dialog.field_x_mm,
                dialog.field_y_mm,
                dialog.field_z_mm,
                dialog.field_r_deg,
            ]
        ):
            return
        # Close the start position dialog
        dialog.accept()
        # Retrieve the coordinate values from the dialog fields
        x_mm = dialog.field_x_mm.text()
        y_mm = dialog.field_y_mm.text()
        z_mm = dialog.field_z_mm.text()
        r_deg = dialog.field_r_deg.text()
        # Get values from the GUI fields
        laser_value = self.get_selected_radio_value()

        power_percent_value = self.laser_power.text()
        z_depth_value = dialog.z_search_depth.text()
        data_storage = self.data_storage.text()

        # Check the starting position fields to update the text file that stores the starting position.
        updated_position = {
            self.microscope_connection.instrument_name: {
                "x(mm)": x_mm,
                "y(mm)": y_mm,
                "z(mm)": z_mm,
                "r(°)": r_deg,
            }
        }
        print(f"{self.microscope_connection.instrument_name}_start_position.txt")
        # print(updated_position)
        # Save the updated position to a text file
        txt.dict_to_text(
            str(
                os.path.join(
                    "microscope_settings",
                    f"{self.microscope_connection.instrument_name}_start_position.txt",
                )
            ),
            updated_position,
        )

        # Get the starting position and save it to xyzr_init
        xyzr_init = [
            x_mm,
            y_mm,
            z_mm,
            r_deg,
        ]
        # Ensure an int is passed for downstream use
        sample_count = int(dialog.sample_count.text())
        # Create a thread for the locate_sample function
        self.locate_sample_thread = Thread(
            target=locate_sample,
            args=(
                self.microscope_connection.connection_data,
                self.sample_name.text(),
                sample_count,
                xyzr_init,
                visualize_event,
                other_data_queue,
                image_queue,
                command_queue,
                system_idle,
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
        self.locate_sample_thread.daemon = True

        # Start the thread
        self.locate_sample_thread.start()
        self.threads.append(self.locate_sample_thread)

    def close_program(self):
        """
        This function closes the program and shuts down the connection. It sets the terminate event, quits the application,
        shuts down the connection, and closes the window.

        Returns
        -------
        None
        """
        print("Stop and close program button pressed")
        (
            live_listen_thread_var,
            command_listen_thread_var,
            send_thread_var,
            processing_thread_var,
        ) = self.microscope_connection.threads
        nuc_client, live_client, _, _, _ = self.microscope_connection.connection_data

        # Set the terminate event
        terminate_event.set()

        # Quit the application
        QApplication.instance().quit()

        # Shut down the connection
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

        # Close the window
        self.close()

    def take_snapshot(self):
        """
        This function initiates the process of taking a snapshot at the current position. It checks the validity of the
        position fields, retrieves values from the GUI fields, and starts a thread for the take_snapshot function.

        Returns
        -------
        None
        """
        # Protect against double clicks on the button
        # Make sure the thread is not already active if the function is attempted a second time
        print("Take snapshot button pressed")
        if self.check_for_active_thread():
            print("Something else is still running!")
            return

        # Check if the fields for the position are valid
        if not self.check_field(
            [self.field_x_mm, self.field_y_mm, self.field_z_mm, self.field_r_deg]
        ):
            return

        # Get values from the GUI fields
        xyzr = [
            self.field_x_mm.text(),
            self.field_y_mm.text(),
            self.field_z_mm.text(),
            self.field_r_deg.text(),
        ]
        laser_value = self.get_selected_radio_value()
        if laser_value is False:
            return
        power_percent_value = self.laser_power.text()

        # Create a thread for the take_snapshot function
        self.take_snapshot_thread = Thread(
            target=take_snapshot,
            args=(
                self.microscope_connection.connection_data,
                xyzr,
                visualize_event,
                other_data_queue,
                image_queue,
                command_queue,
                stage_location_queue,
                send_event,
                laser_value,
                power_percent_value,
            ),
        )

        # Set the thread as a daemon (exits when the main program exits)
        self.take_snapshot_thread.daemon = True

        # Start the thread
        self.take_snapshot_thread.start()

    def set_home_action(self):
        """
        This function sets the 'Home' coordinates to the currently displayed xyzr under the image.

        Returns
        -------
        None
        """
        print("Setting Home in the Mac software")
        # Make sure the thread is not already active if the function is attempted a second time
        if self.check_for_active_thread():
            print("Something else is still running!")
            return

        # Check if the fields for the position are valid
        if not self.check_field(
            [self.current_x, self.current_y, self.current_z, self.current_r]
        ):
            return

        # Get values from the GUI fields
        xyzr_init = [
            float(self.current_x.text().split(" ")[1]),
            float(self.current_y.text().split(" ")[1]),
            float(self.current_z.text().split(" ")[1]),
            float(self.current_r.text().split(" ")[1]),
        ]

        # Create a thread for the set_home function
        self.set_home_thread = Thread(
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
        self.set_home_thread.daemon = True

        # Start the thread
        self.set_home_thread.start()
        self.threads.append(self.set_home_thread)

    def copy_current_position(self):
        """
        This function copies the current position of the microscope to the cyan colored active fields. If the current
        position fields are empty, it copies the initial positions instead.

        Returns
        -------
        None
        """
        # Check if the current position fields are empty
        print("Copy current image position to cyan fields pressed")
        if not all(
            [
                self.current_x.text(),
                self.current_y.text(),
                self.current_z.text(),
                self.current_r.text(),
            ]
        ):
            # If the current position fields are empty, copy the initial positions instead
            if self.check_field(
                [
                    self.start_field_x_mm,
                    self.start_field_y_mm,
                    self.start_field_z_mm,
                    self.start_field_r_deg,
                ]
            ):
                self.field_x_mm.setText(str(self.start_field_x_mm.text()))
                self.field_y_mm.setText(str(self.start_field_y_mm.text()))
                self.field_z_mm.setText(str(self.start_field_z_mm.text()))
                self.field_r_deg.setText(str(self.start_field_r_deg.text()))
            return
        # Copy the current position to the active fields
        self.field_x_mm.setText(str(self.current_x.text().split(" ")[1]))
        self.field_y_mm.setText(str(self.current_y.text().split(" ")[1]))
        self.field_z_mm.setText(str(self.current_z.text().split(" ")[1]))
        self.field_r_deg.setText(str(self.current_r.text().split(" ")[1]))

    def cancel_action(self):
        """
        This function sets the terminate event, effectively signaling all GUI-BASED running threads to stop their operations.
        It does not interfere with the threads maintaining the connection to the microscope, defined in threads.py

        Returns
        -------
        None
        """
        commands = txt.text_to_dict(
            os.path.join("src", "py2flamingo", "functions", "command_list.txt")
        )
        COMMAND_CODES_CAMERA_WORK_FLOW_STOP = int(
            commands["CommandCodes.h"]["COMMAND_CODES_CAMERA_WORK_FLOW_STOP"]
        )
        print("Cancel buttons pressed to set terminate event.")
        terminate_event.set()
        command_queue.put(COMMAND_CODES_CAMERA_WORK_FLOW_STOP)
        send_event.set()

    # TODO add dialog step to ask for the angle step size
    def trace_ellipse_action(self):
        print("Trace Ellipse button pressed")
        angle_step_size_deg = 20
        """
        This function starts at the currently located sample (requires confirmation that the current location is good),
        then proceeds to rotate the sample 120 degrees twice and focus through Z to find three points that can be used
        to define an ellipse that traces out the center of the sample as it turns.
        Assumption to be tested: The max intensity position (focus) will be roughly the same in each orientation.

        Goal: The traced ellipse, along with the dimensions of the sample, can be used for a multi angle acquisition.
        """
        print("Trace ellipse button pressed")
        # Make sure the thread is not already active if the function is attempted a second time
        if self.check_for_active_thread():
            print("Something else is still running!")
            return

        # Check if the sample name is a valid file name
        if not txt.is_valid_filename(self.sample_name.text()):
            show_warning_message("The sample name is not a valid file name")
            return
        # Check if the sample folder exists, and create it if not
        if not os.path.exists(os.path.join("sample_txt", self.sample_name.text())):
            os.makedirs(os.path.join("sample_txt", self.sample_name.text()))
        # Get values from the GUI fields
        laser_value = self.get_selected_radio_value()
        if laser_value is False:
            return
        power_percent_value = self.laser_power.text()
        z_depth_value = self.microscope_connection.z_default
        data_storage = self.data_storage.text()
        # all functions must be run on a separate thread, targetting a function, including its arguments, and then start()
        # Create a thread for the trace_ellipse function
        self.trace_ellipse_thread = Thread(
            target=trace_ellipse,
            args=(
                self.microscope_connection.connection_data,
                visualize_event,
                other_data_queue,
                image_queue,
                command_queue,
                command_data_queue,
                system_idle,
                processing_event,
                send_event,
                terminate_event,
                stage_location_queue,
                angle_step_size_deg,
                self.sample_name.text(),
                laser_value,
                power_percent_value,
                z_depth_value,
                data_storage,
            ),
        )

        # Set the thread as a daemon (exits when the main program exits)
        self.trace_ellipse_thread.daemon = True
        print("starting ellipse thread")
        # Start the thread
        self.trace_ellipse_thread.start()
        self.threads.append(self.trace_ellipse_thread)

    def predict_sample_focus_at_angle(self):
        print("Predict sample location at angle selected")
        if self.check_for_active_thread():
            print("Something else is still running!")
            return

        # Check if the fields for the position are valid
        if not self.check_field([self.field_y_mm, self.field_r_deg]):
            return
        # Get values from the GUI fields
        # get the current angle
        r_deg = float(self.field_r_deg.text())
        if 0 > r_deg > 360:
            show_warning_message("The angle chosen is outside the 0 to 360 range.")
            return

        # Check if the parameter file exists
        if not os.path.exists(
            os.path.join(
                "sample_txt",
                self.sample_name.text(),
                f"top_bounds_{self.sample_name.text()}.txt",
            )
        ):
            show_warning_message(
                f"{self.sample_name.text()}_ellipse_params.txt not found in sample_txt/{self.sample_name.text()}"
            )
            return
        if not os.path.exists(
            os.path.join(
                "sample_txt",
                self.sample_name.text(),
                f"bottom_bounds_{self.sample_name.text()}.txt",
            )
        ):
            show_warning_message(
                f"{self.sample_name.text()}_ellipse_params.txt not found in sample_txt/{self.sample_name.text()}"
            )
            return
        top_bounds_dict = txt.text_to_dict(
            os.path.join(
                "sample_txt",
                self.sample_name.text(),
                f"top_bounds_{self.sample_name.text()}.txt",
            )
        )
        bottom_bounds_dict = txt.text_to_dict(
            os.path.join(
                "sample_txt",
                self.sample_name.text(),
                f"bottom_bounds_{self.sample_name.text()}.txt",
            )
        )
        top_points = txt.dict_to_bounds(top_bounds_dict)
        bottom_points = txt.dict_to_bounds(bottom_bounds_dict)
        top_bound_at_angle = calc.bounding_point_from_angle(top_points, r_deg)
        bottom_bound_at_angle = calc.bounding_point_from_angle(bottom_points, r_deg)
        _, _, _, image_pixel_size_mm, frame_size = scope.initial_setup(
            command_queue, other_data_queue, send_event
        )
        center_point = calc.find_center(
            top_bound_at_angle,
            bottom_bound_at_angle,
            shift=frame_size * image_pixel_size_mm / 2,
        )

        self.field_x_mm.setText(str(round(center_point[0], 3)))
        self.field_y_mm.setText(str(round(center_point[1], 3)))
        self.field_z_mm.setText(str(round(center_point[2], 3)))
        self.take_snapshot()

    # TODO Need to handle cases with more than one laser collection - maybe base on MultiAngle.txt??
    def multi_angle_dialog(self):
        print("Multi-angle collection button pressed")
        """
        This function creates a dialog to collect information specifically needed to collect a multi-angle acquisition.
        1. Validate start position (bounding box)
        2. Angle increment for collection
        3. Text file as the basis for collection exists
        4. Validate saved file location and comments/folder descriptions

        Returns
        -------
        None
        """
        # Get the bounding box from a text file

        # # Create the coordinate dialog
        if not os.path.exists(
            os.path.join(
                "sample_txt",
                self.sample_name.text(),
                f"top_bounds_{self.sample_name.text()}.txt",
            )
        ):
            show_warning_message(
                f"{self.sample_name.text()} bounds text file not found in sample_txt/{self.sample_name.text()} \n The sample name in the original dialog must match an existing bounds file."
            )
            return
        if not os.path.exists(
            os.path.join(
                "sample_txt",
                self.sample_name.text(),
                f"bottom_bounds_{self.sample_name.text()}.txt",
            )
        ):
            show_warning_message(
                f"{self.sample_name.text()} bounds text file not found in sample_txt/{self.sample_name.text()}. \n The sample name in the original dialog must match an existing bounds file."
            )
            return
        # Create start_bounds from the two boundary files
        top_bounds_dict = txt.text_to_dict(
            os.path.join(
                "sample_txt",
                self.sample_name.text(),
                f"top_bounds_{self.sample_name.text()}.txt",
            )
        )
        bottom_bounds_dict = txt.text_to_dict(
            os.path.join(
                "sample_txt",
                self.sample_name.text(),
                f"bottom_bounds_{self.sample_name.text()}.txt",
            )
        )
        top_points = txt.dict_to_bounds(top_bounds_dict)
        bottom_points = txt.dict_to_bounds(bottom_bounds_dict)

        dialog = MultiAngleDialog(self.sample_name.text(), self)

        # Connect the "Okay" button's signal to a lambda function that executes the remaining code
        dialog.button_okay.clicked.connect(
            lambda: self.multi_angle_collection(dialog, top_points, bottom_points)
        )

        # Show the dialog
        if dialog.exec() != QDialog.Accepted:
            # if Okay was not clicked, do not proceed with the function.
            return

    def multi_angle_collection(self, dialog, top_points, bottom_points):
        # Make sure the thread is not already active if the function is attempted a second time
        if self.check_for_active_thread():
            print("Something else is still running!")
            return
        sample_name = dialog.field_sample_name.text()
        angle_step_size_deg = float(dialog.field_increment_angle.text())
        workflow_filename = dialog.field_workflow_source.text()
        comment = dialog.field_sample_comment.text()
        print(f"increment angle found to be {angle_step_size_deg}")
        print(f"workflow_name is: {workflow_filename}")
        # Close the starting dialog
        dialog.accept()
        # check that the workflow file exists
        if not os.path.exists(os.path.join("workflows", workflow_filename)):
            show_warning_message(
                f"{workflow_filename} not found in the workflows folder!"
            )
            return

        # Create a thread for the trace_ellipse function
        self.multi_angle_collection_thread = Thread(
            target=multi_angle_collection,
            args=(
                self.microscope_connection.connection_data,
                visualize_event,
                other_data_queue,
                image_queue,
                command_queue,
                command_data_queue,
                system_idle,
                processing_event,
                send_event,
                terminate_event,
                stage_location_queue,
                angle_step_size_deg,
                sample_name,
                comment,
                top_points,
                bottom_points,
                workflow_filename,
                # overlap_percent,
            ),
        )

        # Set the thread as a daemon (exits when the main program exits)
        self.multi_angle_collection_thread.daemon = True
        print("starting multi-angle collection thread")
        # Start the thread
        self.multi_angle_collection_thread.start()
        self.threads.append(self.multi_angle_collection_thread)

    def add_your_code(self):  # sourcery skip: remove-redundant-pass
        """
        Docstring here
        """
        # Make sure the thread is not already active if the function is attempted a second time
        if self.check_for_active_thread():
            print("Something else is still running!")
            return

        # all functions must be run on a separate thread, targetting a function, including its arguments, and then start()
        pass

    # incomplete
    def basic_workflow_action(self):
        """
        We need two things for a basic workflow. 
        1. The settings, probably based off an existing workflow file OR GUI settings
        2. The location to scan, which should be two sets of x,y,z,r coordinates, where the coordinates represent the upper left corner of the scan location.
        """
        # Make sure the thread is not already active if the function is attempted a second time
        if self.check_for_active_thread():
            print("Something else is still running!")
            return

        # Get the default folder path for the file dialog
        default_folder = os.path.join(os.getcwd(), "sample_txt")

        # Open a file dialog to select the boudaries file
        file_dialog = QFileDialog()
        file_dialog.setFileMode(QFileDialog.ExistingFile)
        file_dialog.setNameFilter("Text files (*.txt)")
        file_dialog.setWindowTitle(
            "Select a boundary file that contains the coordinates you would like to scan"
        )
        # Set the default folder if it exists
        if os.path.exists(default_folder):
            file_dialog.setDirectory(default_folder)

        if file_dialog.exec_():
            if selected_files := file_dialog.selectedFiles():
                boundary_file = selected_files[0]
                boundary_dict = txt.text_to_dict(boundary_file)
                top_bound, bottom_bound = txt.dict_to_bounds(boundary_dict)
            else:
                print(
                    "Either the workflow or boundaries were not found, exiting function."
                )
                return
        ###############
        default_folder = os.path.join(os.getcwd(), "workflows")

        # Open a file dialog to select the boudaries file
        file_dialog = QFileDialog()
        file_dialog.setFileMode(QFileDialog.ExistingFile)
        file_dialog.setNameFilter("Text files (*.txt)")
        file_dialog.setWindowTitle("Select a workflow file to base your scan on")
        # Set the default folder if it exists
        if os.path.exists(default_folder):
            file_dialog.setDirectory(default_folder)

        if file_dialog.exec_():
            if selected_files := file_dialog.selectedFiles():
                workflow_file = selected_files[0]
                workflow_dict = txt.workflow_to_dict(workflow_file)
            else:
                print(
                    "Either the workflow or boundaries were not found, exiting function."
                )
                return

        workflow_dict = txt.dict_positions(
            workflow_dict, top_bound, bottom_bound, save_with_data=True
        )
        # Create a thread for the run workflow
        self.run_workflow = Thread(
            target=run_workflow,
            args=(
                self.microscope_connection.connection_data,
                self.sample_name.text(),
                workflow_dict,
                visualize_event,
                image_queue,
                command_queue,
                stage_location_queue,
                send_event,
            ),
        )

        # Set the thread as a daemon (exits when the main program exits)
        self.run_workflow.daemon = True

        # Start the thread
        self.run_workflow.start()
