#TO DO? Create initial dialog to ask about which microscope to connect to. Create named files based on the microscope (settings, workflows)
#TO DO? Does not handle failing to connect gracefully, though it at least creates a pop up dialog informing the user that it failed.
import sys, os, shutil, time
from PyQt5.QtWidgets import QApplication, QMainWindow, QLabel, QLineEdit, QPushButton 
from PyQt5.QtWidgets import QRadioButton, QFileDialog, QMessageBox, QWidget, QVBoxLayout, QFormLayout, QHBoxLayout
from PyQt5.QtGui import QPixmap, QDoubleValidator
from PyQt5.QtCore import Qt, QSize, QTimer
from locate_sample import locate_sample
from take_snapshot import take_snapshot
from go_to_position import go_to_position
from functions.image_display import convert_to_qimage
from queue import Queue
from threading import Event, Thread
from functions.text_file_parsing import workflow_to_dict, text_to_dict, dict_to_text
from functions.image_display import convert_to_qimage
from functions.microscope_connect import close_connection, start_connection, create_threads
#from PIL import Image
import numpy as np



#Set up some queues and events to keep track of what is happening across multiple threads
###################################
image_queue, command_queue, z_plane_queue, intensity_queue, visualize_queue = Queue(), Queue(), Queue(), Queue(), Queue()
view_snapshot, system_idle, processing_event, send_event, terminate_event = Event(), Event(), Event(), Event(), Event()
visualize_event = Event()
#queues to send data with commands. Possibly condense this
command_data_queue = Queue()
stage_location_queue, other_data_queue = Queue(), Queue()
######################################

#Need to:
# Handle the snapshot/get a snapshot workflow?
class FlamingoController(QMainWindow):
    def __init__(self):
        super().__init__()
        #Blank values that may not be necessary but made some debugging easier so they were left in.
        self.lasers = []
        self.start_position = ['','','','']
        self.selected_laser = ''
        self.laser_power = ''
        self.data_storage_location = ''
        self.current_coordinates = []
        self.check_folders()
        # Check if 'ZStack.txt' 'ScopeSettings 'FlamingoMetaData' files exist, otherwise prompt user to select a file
        self.check_metadata_file_and_connect()
        self.check_settings_file()
        self.check_zstack_file()
        self.check_start_position()
        self.setWindowTitle(f"Flamingo Controller: {self.instrument_name}")
        self.setGeometry(200, 200, 600, 550)

        # Create labels and fields
        self.create_image_label()  # Create the QLabel widget for displaying the image
        self.create_GUI_layout()
        #self.create_buttons()
        self.update_display_timer()

    def create_image_label(self):
        self.image_label = QLabel(self)  # Create the QLabel widget
        self.image_label.setAlignment(Qt.AlignCenter)  # Center-align the image within the label

        #self.image_label.setGeometry(400, 1000, 512, 512)  # Set the position and size of the label within the main window

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
                image = visualize_queue.get()
                self.display_image(image)
                print(f'image to display is {image.shape} and {image.dtype}')
                # Update the display with the image
                if not stage_location_queue.empty():
                    #in case data transfer was slow, get the last shown image location only.
                    while stage_location_queue.qsize() > 1:
                        stage_location_queue.get()
                    self.current_coordinates = stage_location_queue.get()

                    self.current_x.setText(f'x: {round(float(self.current_coordinates[0]),3)}')
                    self.current_y.setText(f'y: {round(float(self.current_coordinates[1]),3)}')
                    self.current_z.setText(f'z: {round(float(self.current_coordinates[2]),3)}')
                    self.current_r.setText(f'r: {round(float(self.current_coordinates[3]),3)}')
            else:
                image = visualize_queue.get()
                    
    def display_image(self, image):
        #print('display_image')
        # Convert the numpy array image to QImage
        q_image = convert_to_qimage(image)

        # Create a pixmap and set it as the label's pixmap
        pixmap = QPixmap.fromImage(q_image)
        pixmap = pixmap.scaled(QSize(512, 512), Qt.KeepAspectRatio)
        self.image_label.setPixmap(pixmap)
    def check_folders(self):
        if not os.path.exists("workflows"):
            os.makedirs("workflows")
        if not os.path.exists("ouput_png"):
            os.makedirs("ouput_png")
    def check_settings_file(self):
        # Define the file path for the ScopeSettings.txt file
        file_path = os.path.join("microscope_settings", "ScopeSettings.txt")
        if not os.path.exists(file_path):  # Check if the file does not exist
            # Show a message box with a custom message
            message_box = QMessageBox()
            message_box.setIcon(QMessageBox.Warning)
            message_box.setWindowTitle("File Not Found")
            message_box.setText("The file ScopeSettings.txt was not found at microscope_settings/ScopeSettings.txt. \nPlease locate a ScopeSettings text file to use. One should be generated when a workflow is manually run on the microscope.")
            message_box.setStandardButtons(QMessageBox.Ok)
            message_box.exec_()

            # Prompt user to select a text file
            file_dialog = QFileDialog()
            file_dialog.setWindowTitle("Select ScopeSettings Text File")
            file_dialog.setFileMode(QFileDialog.ExistingFile)
            file_dialog.setNameFilter("Text files (*.txt)")
            if file_dialog.exec_():
                selected_file = file_dialog.selectedFiles()[0]  # Get the selected file path
                os.makedirs("microscope_settings", exist_ok=True)  # Create the 'microscope_settings' directory if it doesn't exist
                try:
                    shutil.copy(selected_file, file_path)  # Copy the selected file to the 'microscope_settings' directory
                except OSError as e:
                    QMessageBox.warning(self, "Error", str(e))
            else:
                QMessageBox.warning(self, "Warning", "ScopeSettings.txt file not found! Closing.")
    def check_start_position(self):
        file_path = os.path.join("microscope_settings", f"{self.instrument_name}_start_position.txt")
        if not os.path.exists(file_path):  # Check if the file does not exist
            # Show a message box with a custom message
            message_box = QMessageBox()
            message_box.setIcon(QMessageBox.Warning)
            message_box.setWindowTitle("File Not Found")
            message_box.setText(f"The file {self.instrument_name}_start_position.txt was not found in the 'microscope_settings' folder. \nYou will need to add the start position through the GUI to use the Find Sample funciton.")
            message_box.setStandardButtons(QMessageBox.Ok)
            message_box.exec_()
        else:
            positions_dict = text_to_dict(f"microscope_settings/{self.instrument_name}_start_position.txt")
            self.start_position = [float(positions_dict[self.instrument_name]['x']), float(positions_dict[self.instrument_name]['y']),float(positions_dict[self.instrument_name]['z']),float(positions_dict[self.instrument_name]['r'])] 

    def check_metadata_file_and_connect(self):
        # Define the file path for the FlamingoMetaData.txt file
        file_path = os.path.join("microscope_settings", "FlamingoMetaData.txt")
        if not os.path.exists(file_path):  # Check if the file does not exist
            # Show a message box with a custom message
            message_box = QMessageBox()
            message_box.setIcon(QMessageBox.Warning)
            message_box.setWindowTitle("File Not Found")
            message_box.setText("The file FlamingoMetaData.txt was not found at microscope_settings/FlamingoMetadata.txt. \nPlease locate a Metadata text file to use as the basis for your microscope (e.g. IP address, tube length). One should be generated when a workflow is manually run on the microscope.")
            message_box.setStandardButtons(QMessageBox.Ok)
            message_box.exec_()

            # Prompt user to select a text file
            file_dialog = QFileDialog()
            file_dialog.setWindowTitle("Select Metadata Text File")
            file_dialog.setFileMode(QFileDialog.ExistingFile)
            file_dialog.setNameFilter("Text files (*.txt)")
            if file_dialog.exec_():
                selected_file = file_dialog.selectedFiles()[0]  # Get the selected file path
                os.makedirs("microscope_settings", exist_ok=True)  # Create the 'microscope_settings' directory if it doesn't exist
                try:
                    shutil.copy(selected_file, file_path)  # Copy the selected file to the 'microscope_settings' directory
                except OSError as e:
                    QMessageBox.warning(self, "Error", str(e))
            else:
                QMessageBox.warning(self, "Warning", "FlamingoMetaData.txt file not found! Closing.")

        # Read the text file to determine the default values for the dialog box
        settings_dict = text_to_dict(file_path)  # Convert the metadata text file to a dictionary
        self.IP = settings_dict['Instrument']['Type']['Microscope address'].split(' ')[0]  # Get the IP address from the dictionary
        self.port = int(settings_dict['Instrument']['Type']['Microscope address'].split(' ')[1])  # Get the port from the dictionary
        self.instrument_name = settings_dict['Instrument']['Type']['Microscope name']  # Get the microscope name from the dictionary
        ## Currently non-functional as the text file always gives all models.
        self.instrument_type = settings_dict['Instrument']['Type']['Microscope type']  # Get the microscope type from the dictionary


        #Use the information from the FlamingoMetaData.txt file to start the connection with the correct instrument.
        nuc_client, live_client, wf_zstack, LED_on, LED_off = start_connection(self.IP,self.port)
        self.connection_data = [nuc_client, live_client, wf_zstack, LED_on, LED_off]
        live_listen_thread_var, command_listen_thread_var, send_thread_var, processing_thread_var = create_threads(
            nuc_client, live_client, other_data_queue, image_queue, command_queue, z_plane_queue, intensity_queue, visualize_queue,
            system_idle, processing_event, send_event, terminate_event, command_data_queue,
            stage_location_queue
        )
        self.threads = [live_listen_thread_var, command_listen_thread_var, send_thread_var, processing_thread_var]

    def check_zstack_file(self):
        file_path = os.path.join("workflows", "ZStack.txt")  # Define the file path for the ZStack.txt file
        if not os.path.exists(file_path):  # Check if the file does not exist
            # Show a message box with a custom message
            message_box = QMessageBox()
            message_box.setIcon(QMessageBox.Warning)
            message_box.setWindowTitle("File Not Found")
            message_box.setText("The file ZStack.txt was not found at workflows/ZStack.txt. \nPlease locate a workflow text (workflow.txt) file to use as the basis for your settings (Laser line, laser power). One should be generated when a workflow is manually run on the microscope.")
            message_box.setStandardButtons(QMessageBox.Ok)
            message_box.exec_()

            # Prompt user to select a text file
            file_dialog = QFileDialog()
            file_dialog.setWindowTitle("Select Workflow Text File")
            file_dialog.setFileMode(QFileDialog.ExistingFile)
            file_dialog.setNameFilter("Text files (*.txt)")
            if file_dialog.exec_():
                selected_file = file_dialog.selectedFiles()[0]  # Get the selected file path
                os.makedirs("workflows", exist_ok=True)  # Create the 'workflows' directory if it doesn't exist
                try:
                    shutil.copy(selected_file, file_path)  # Copy the selected file to the 'workflows' directory
                except OSError as e:
                    QMessageBox.warning(self, "Error", str(e))
            else:
                QMessageBox.warning(self, "Warning", "ZStack.txt file not found! Closing.")

        # Read the text file to determine the default values for the dialog box
        zdict = workflow_to_dict(file_path)  # Convert the workflow text file to a dictionary
        self.lasers =  zdict['Illumination Source']  # Get the laser sources from the dictionary
        for laser in self.lasers:
            if zdict['Illumination Source'][laser].split(' ')[1] == '1':
                self.selected_laser = laser  # Set the selected laser based on the dictionary value
                self.laser_power = zdict['Illumination Source'][laser].split(' ')[0]  # Set the laser power based on the dictionary value

        self.lasers = [entry for entry in self.lasers if 'laser' in entry.lower()]  # Filter the lasers list based on specific condition
        self.data_storage_location = zdict['Experiment Settings']['Save image drive']  # Get the data storage location from the dictionary
    
    def create_GUI_layout(self):
        layout = QHBoxLayout()  # Create a QHBoxLayout for the main layout
        radio_layout = QHBoxLayout()  # Create a QHBoxLayout for radio buttons
        button_layout = QVBoxLayout()  # Create a QVBoxLayout for buttons
        form_layout = QFormLayout()  # Create a QFormLayout for form fields
        start_position_layout = QHBoxLayout() # Create a QHBoxLayout for the tip of the sample holder
        current_position_layout = QHBoxLayout() # Create a QHBoxLayout for the current position
        image_layout = QVBoxLayout()  # Create a QVBoxLayout for the image plus coordinates
        vertical_layout = QVBoxLayout()  # Create a QVBoxLayout to combine the radio buttons, form fields, and buttons
        image_layout.setAlignment(Qt.AlignTop)
        image_layout.addWidget(self.image_label)  # Add the image label to the image layout
        #initialize current position values with the starting position
        self.current_x = QLabel()
        self.current_y = QLabel()
        self.current_z = QLabel()
        self.current_r = QLabel()
        current_position_layout.addWidget(self.current_x)
        current_position_layout.addWidget(self.current_y)
        current_position_layout.addWidget(self.current_z)
        current_position_layout.addWidget(self.current_r)
        image_layout.addLayout(current_position_layout)
        # Get labels from the parsed metadata file
        labels = self.lasers
        self.radio_buttons = []
        for label in labels:
            radio_button = QRadioButton(label)  # Create a QRadioButton for each label
            if label == self.selected_laser:
                radio_button.setChecked(True)  # Set the checked state based on the selected laser
            self.radio_buttons.append(radio_button)  # Add the radio button to the list
            radio_layout.addWidget(radio_button)  # Add the radio button to the radio layout

        # Create the form fields with their default values
        self.IP_address = self.create_field(is_string=True, default_value=self.IP)
        self.command_port = self.create_field(default_value=self.port)
        self.laser_power_field = self.create_field(is_string=True, default_value=self.laser_power)
        self.z_search_depth_field = self.create_field(default_value="2.0")
        self.data_storage_field = self.create_field(is_string=True, default_value=self.data_storage_location)

        # Add the form fields to the form layout
        form_layout.addRow(f"{self.instrument_name} IP Address", self.IP_address)
        form_layout.addRow(f"{self.instrument_name} command port (default 53717)", self.command_port)
        form_layout.addRow("Laser Power Percent:", self.laser_power_field)
        form_layout.addRow("Z Search Depth (mm):", self.z_search_depth_field)
        form_layout.addRow("Data storage path:", self.data_storage_field)
        ##Add in HBox for information
        #form_layout.addRow("Initial XYZR coordinate for 'Find Sample'. Also Snapshot position.")

        # Create form fields for sample start location
        self.label_x = QLabel("x: ")
        self.field_x = self.create_field(default_value=self.start_position[0])

        self.label_y = QLabel("y: ")
        self.field_y = self.create_field(default_value=self.start_position[1])

        self.label_z = QLabel("z: ")
        self.field_z = self.create_field(default_value=self.start_position[2])

        self.label_r = QLabel("r: ")
        self.field_r = self.create_field(default_value=self.start_position[3])
        #Set current coordinates
        self.current_coordinates = [self.start_position[0],self.start_position[1],self.start_position[2],self.start_position[3]]

        # Add form fields and labels to the layout
        start_position_layout.addWidget(self.label_x)
        start_position_layout.addWidget(self.field_x)
        start_position_layout.addWidget(self.label_y)
        start_position_layout.addWidget(self.field_y)
        start_position_layout.addWidget(self.label_z)
        start_position_layout.addWidget(self.field_z)
        start_position_layout.addWidget(self.label_r)
        start_position_layout.addWidget(self.field_r)

        # Create and connect the buttons
        locate_sample_button = QPushButton("Find Sample", self)
        # locate_sample_button.clicked.connect(self.update_image_window)  # Uncomment and update the slot function
        locate_sample_button.clicked.connect(self.locate_sample_action)
        go_to_position_button = QPushButton("Go to XYZR", self)
        go_to_position_button.clicked.connect(self.go_to_position)
        take_snapshot_button = QPushButton("Take IF snapshot", self)
        take_snapshot_button.setToolTip("Takes a snapshot using the currently selected fluorescence settings and location")
        take_snapshot_button.clicked.connect(self.take_snapshot)
        duplicate_and_add_your_button = QPushButton("Duplicate and add your button", self)
        duplicate_and_add_your_button.clicked.connect(self.add_your_code)
        cancel_button = QPushButton("Stop AND CLOSE PROGRAM", self)
        cancel_button.clicked.connect(self.cancel_program)

        # Add the buttons to the button layout
        # This controls the order they show up on screen
        button_layout.addWidget(locate_sample_button)
        button_layout.addWidget(go_to_position_button)
        button_layout.addWidget(take_snapshot_button)
        button_layout.addWidget(duplicate_and_add_your_button)
        button_layout.addWidget(cancel_button)

        # Add the combined vertical layouts (radio buttons, form fields, and buttons) to the main vertical layout
        vertical_layout.setAlignment(Qt.AlignTop)
        vertical_layout.addLayout(radio_layout)
        vertical_layout.addLayout(form_layout)
        vertical_layout.addLayout(start_position_layout)
        vertical_layout.addLayout(button_layout)

        # Add the combined vertical layouts on the left and the image layout on the right to the horizontal layout
        layout.addLayout(vertical_layout)
        layout.addLayout(image_layout)

        central_widget = QWidget()  # Create a QWidget for the central widget
        central_widget.setLayout(layout)  # Set the layout for the central widget
        self.setCentralWidget(central_widget)  # Set the central widget for the main window

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
                message_box.setText("To find the sample requires a starting point (XYZR), generally near the tip of the sample holder. One of the fields is blank.")
                message_box.setStandardButtons(QMessageBox.Ok)
                message_box.exec_()
                return False

            # Attempt to convert the text to a float
            try:
                value = float(text)
                print("Value:", value)
            except ValueError:
                message_box = QMessageBox()
                message_box.setIcon(QMessageBox.Warning)
                message_box.setWindowTitle("Settings required")
                message_box.setText("To find the sample requires a starting point (XYZR), generally near the tip of the sample holder. One of the fields does not contain a numerical value.")
                message_box.setStandardButtons(QMessageBox.Ok)
                message_box.exec_()
                #QMessageBox.warning(central_widget, "Field Error", "Invalid number!")
                return False
        return True
    # def create_buttons(self):
    #     pass
    def go_to_position(self):
      
        if not self.check_field([self.field_x, self.field_y, self.field_z, self.field_r]):
            return
        # Get values from the GUI fields

        xyzr_init = [self.field_x.text(),self.field_y.text(),self.field_z.text(),self.field_r.text()]
        # Create a thread for the locate_sample function
        go_to_position_thread = Thread(target=go_to_position, args=(xyzr_init, command_data_queue, command_queue, send_event))
        
        # Set the thread as a daemon (exits when the main program exits)
        go_to_position_thread.daemon = True
        
        # Start the thread
        go_to_position_thread.start()

    def locate_sample_action(self):
        #Check for a start position

        if not self.check_field([self.field_x, self.field_y, self.field_z, self.field_r]):
            return
        # Get values from the GUI fields
        laser_value = self.get_selected_radio_value()
        power_percent_value = self.laser_power_field.text()
        z_depth_value = self.z_search_depth_field.text()
        data_storage = self.data_storage_field.text()
        updated_position = {
            self.instrument_name: {
                'x': self.field_x.text(),
                'y': self.field_y.text(),
                'z': self.field_z.text(),
                'r': self.field_r.text()
            }
        }
        xyzr_init = [self.field_x.text(),self.field_y.text(),self.field_z.text(),self.field_r.text()]
        dict_to_text(f"microscope_settings/{self.instrument_name}_start_position.txt", updated_position)
        # Create a thread for the locate_sample function
        locate_sample_thread = Thread(target=locate_sample, args=(self.threads, self.connection_data, xyzr_init, visualize_event,
                                                                other_data_queue, image_queue, command_queue,
                                                                z_plane_queue, intensity_queue, 
                                                                system_idle, processing_event, send_event,
                                                                terminate_event, command_data_queue,
                                                                stage_location_queue, laser_value, power_percent_value,
                                                                z_depth_value, data_storage))
        
        # Set the thread as a daemon (exits when the main program exits)
        locate_sample_thread.daemon = True
        
        # Start the thread
        locate_sample_thread.start()



    def get_selected_radio_value(self):
        for radio_button in self.radio_buttons:
            if radio_button.isChecked():
                return radio_button.text()

        return None
        
    def cancel_program(self):
        live_listen_thread_var, command_listen_thread_var, send_thread_var, processing_thread_var= self.threads
        nuc_client, live_client, wf_zstack, LED_on, LED_off = self.connection_data
        terminate_event.set()
        QApplication.instance().quit()
        print('Shutting down connection')
        close_thread = Thread(target = close_connection, args=(nuc_client, live_client,live_listen_thread_var, 
                                                      command_listen_thread_var,send_thread_var,processing_thread_var), group=None)
        close_thread.daemon = True
        close_thread.start()

        self.close()

    def take_snapshot(self):
        # Open the PNG file
        xyzr_init = [self.field_x.text(),self.field_y.text(),self.field_z.text(),self.field_r.text()]
        # print(f'snapshot coordinates {xyzr_init}')
        laser_value = self.get_selected_radio_value()
        power_percent_value = self.laser_power_field.text()
        #all functions must be run on a separate thread, targetting a function, including its arguments, and then start()
        take_snapshot_thread = Thread(target=take_snapshot, args=(self.connection_data, xyzr_init, visualize_event,
                  image_queue,command_queue, send_event, laser_value, power_percent_value))
        # Set the thread as a daemon (exits when the main program exits)
        take_snapshot_thread.daemon = True
        
        # Start the thread
        take_snapshot_thread.start()


        pass
    def add_your_code(self):
        #all functions must be run on a separate thread, targetting a function, including its arguments, and then start()
        pass

if __name__ == "__main__":
    app = QApplication(sys.argv)
    #image_window = ImageWindow()
    controller = FlamingoController()

    controller.show()

    sys.exit(app.exec_())