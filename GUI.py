#TO DO? Create initial dialog to ask about which microscope to connect to. Create named files based on the microscope (settings, workflows)
#TO DO? Does not handle failing to connect gracefully, though it at least creates a pop up dialog informing the user that it failed.
import sys, os, shutil, time, threading
from PyQt5.QtWidgets import QApplication, QMainWindow, QLabel, QLineEdit, QPushButton 
from PyQt5.QtWidgets import QRadioButton, QFileDialog, QMessageBox, QWidget, QVBoxLayout, QFormLayout, QHBoxLayout
from PyQt5.QtGui import QPixmap, QDoubleValidator
#from PyQt5.QtWidgets import QMessageBox
#from PyQt5.QtWidgets import QApplication, QMainWindow, QLabel, QLineEdit, QVBoxLayout, QFormLayout, QPushButton, QHBoxLayout, QRadioButton, QWidget
from PyQt5.QtCore import Qt, QSize, QTimer
from locate_sample import locate_sample
from functions.image_display import convert_to_qimage
from queue import Queue
from threading import Event, Thread
from functions.text_file_parsing import workflow_to_dict, text_to_dict
from functions.image_display import convert_to_qimage

from PIL import Image
import numpy as np



#Set up some queues and events to keep track of what is happening across multiple threads
###################################
image_queue, command_queue, z_plane_queue, intensity_queue, visualize_queue = Queue(), Queue(), Queue(), Queue(), Queue()
view_snapshot, system_idle, processing_event, send_event, terminate_event = Event(), Event(), Event(), Event(), Event()
visualize_event = Event()
#queues to send data with commands. Possibly condense this
data0_queue, data1_queue, data2_queue, value_queue = Queue(),Queue(),Queue(),Queue()
stage_location_queue, other_data_queue = Queue(), Queue()
######################################

#Need to:
# Handle the snapshot/get a snapshot workflow?
class FlamingoController(QMainWindow):
    def __init__(self):
        super().__init__()
        self.lasers = []
        self.selected_laser = ''
        self.laser_power = ''
        self.data_storage_location = ''
        # Check if 'ZStack.txt' and 'FlamingoMetaData' files exists, otherwise prompt user to select a file
        self.check_metadata_file()
        self.check_zstack_file()

        self.setWindowTitle(f"Flamingo Controller: {self.instrument_name}")
        self.setGeometry(200, 200, 600, 550)




        # Create labels and fields
        self.create_image_label()  # Create the QLabel widget for displaying the image
        self.create_fields()
        self.create_buttons()


    def create_image_label(self):
        self.image_label = QLabel(self)  # Create the QLabel widget
        self.image_label.setAlignment(Qt.AlignCenter)  # Center-align the image within the label

        #self.image_label.setGeometry(400, 1000, 512, 512)  # Set the position and size of the label within the main window

    def update_display_thread(self):
        self.display_timer = QTimer()
        self.display_timer.timeout.connect(self.update_display)
        
        # Set the interval for the QTimer (e.g., update every 100 milliseconds)
        update_interval = 100
        self.display_timer.start(update_interval)
    def update_display(self):
            if visualize_event.is_set():
                print('Visualize event received')
                visualize_event.clear()
                # Check if there are images in the queue
                if not visualize_queue.empty():
                    # Get the latest image from the queue
                    image = visualize_queue.get()
                    print(f'image to display is {image.shape} and {image.dtype}')
                    # Update the display with the image
                    self.display_image(image)
                    
    def display_image(self, image):
        print('display_image')
        # Convert the numpy array image to QImage
        q_image = convert_to_qimage(image)

        # Create a pixmap and set it as the label's pixmap
        pixmap = QPixmap.fromImage(q_image)
        pixmap = pixmap.scaled(QSize(512, 512), Qt.KeepAspectRatio)
        self.image_label.setPixmap(pixmap)
    

    def check_metadata_file(self):
        #replace with check of microscope specific file
        file_path = os.path.join("microscope_settings", "FlamingoMetaData.txt")
        if not os.path.exists(file_path):
            # Show a message box with a custom message
            message_box = QMessageBox()
            message_box.setIcon(QMessageBox.Warning)
            message_box.setWindowTitle("File Not Found")
            message_box.setText("The file FlamingoMetaData.txt was not found at microscope_settings/FlamingoMetadata.txt. \nPlease locate a Metadata text file to use as the basis for your microscope (e.g. IP address, tube length).")
            message_box.setStandardButtons(QMessageBox.Ok)
            message_box.exec_()

            # Prompt user to select a text file
            file_dialog = QFileDialog()
            file_dialog.setWindowTitle("Select Metadata Text File")
            file_dialog.setFileMode(QFileDialog.ExistingFile)
            file_dialog.setNameFilter("Text files (*.txt)")
            if file_dialog.exec_():
                selected_file = file_dialog.selectedFiles()[0]
                os.makedirs("microscope_settings", exist_ok=True)
                try:
                    shutil.copy(selected_file, file_path)  # Copy the file to the new location
                except OSError as e:
                    QMessageBox.warning(self, "Error", str(e))
            else:
                QMessageBox.warning(self, "Warning", "ZStack.txt file not found! Closing.")
        #Read the text file to determine the default values for the dialog box
        settings_dict = text_to_dict(file_path)
        self.IP =  settings_dict['Instrument']['Type']['Microscope address'].split(' ')[0]
        self.port = settings_dict['Instrument']['Type']['Microscope address'].split(' ')[1]
        self.instrument_name = settings_dict['Instrument']['Type']['Microscope name']
        ##Currently non-functional as the text file always gives all models.
        self.instrument_type = settings_dict['Instrument']['Type']['Microscope type']


    def check_zstack_file(self):
        file_path = os.path.join("workflows", "ZStack.txt")
        if not os.path.exists(file_path):
            # Show a message box with a custom message
            message_box = QMessageBox()
            message_box.setIcon(QMessageBox.Warning)
            message_box.setWindowTitle("File Not Found")
            message_box.setText("The file ZStack.txt was not found at workflows/ZStack.txt. \nPlease locate a workflow text file to use as the basis for your settings (Laser line, laser power, etc.).")
            message_box.setStandardButtons(QMessageBox.Ok)
            message_box.exec_()

            # Prompt user to select a text file
            file_dialog = QFileDialog()
            file_dialog.setWindowTitle("Select Workflow Text File")
            file_dialog.setFileMode(QFileDialog.ExistingFile)
            file_dialog.setNameFilter("Text files (*.txt)")
            if file_dialog.exec_():
                selected_file = file_dialog.selectedFiles()[0]
                os.makedirs("workflows", exist_ok=True)
                try:
                    shutil.copy(selected_file, file_path)  # Copy the file to the new location
                except OSError as e:
                    QMessageBox.warning(self, "Error", str(e))
            else:
                QMessageBox.warning(self, "Warning", "ZStack.txt file not found! Closing.")
        #Read the text file to determine the default values for the dialog box
        zdict = workflow_to_dict(file_path)
        self.lasers =  zdict['Illumination Source']
        for laser in self.lasers:
            #print(zdict['Illumination Source'][laser])
            if zdict['Illumination Source'][laser].split(' ')[1] == '1':
                self.selected_laser = laser
                self.laser_power = zdict['Illumination Source'][laser].split(' ')[0]
        self.lasers = [entry for entry in self.lasers if 'laser' in entry.lower()]
        self.data_storage_location = zdict['Experiment Settings']['Save image drive'] 
        #print(self.lasers)


    def create_fields(self):
        layout = QHBoxLayout()
        #layout.addStretch()
        radio_layout = QHBoxLayout()
        button_layout = QVBoxLayout()
        form_layout = QFormLayout()
        image_layout = QHBoxLayout()

        vertical_layout = QVBoxLayout()
        #vertical_layout.addStretch()
        image_layout.addWidget(self.image_label)

        # Get labels from a function (replace this with your own function)
        labels = self.lasers
        self.radio_buttons = []
        for label in labels:
            radio_button = QRadioButton(label)
            if label == self.selected_laser:
                radio_button.setChecked(True)
            self.radio_buttons.append(radio_button)
            radio_layout.addWidget(radio_button)



        ####### Create the fields and their default values
        self.IP_address = self.create_field(f"{self.instrument_name} IP Address", is_string=True, default_value=self.IP)
        self.command_port = self.create_field(f"{self.instrument_name} command port (default 53717)", is_string=True, default_value=self.port)
        self.laser_power_field = self.create_field("Laser Power Percent", is_string=True, default_value=self.laser_power)
        self.z_search_depth_field = self.create_field("Z Search Depth (mm)", default_value="2.0")
        self.data_storage_field = self.create_field("Data storage path:", is_string=True, default_value=self.data_storage_location)
        ####### Add the fields to the form layout
        form_layout.addRow(f"{self.instrument_name} IP Address", self.IP_address)
        form_layout.addRow(f"{self.instrument_name} command port (default 53717)", self.command_port)
        form_layout.addRow("Laser Power Percent:", self.laser_power_field)
        form_layout.addRow("Z Search Depth (mm):", self.z_search_depth_field)
        form_layout.addRow("Data storage path:", self.data_storage_field)

        locate_sample_button = QPushButton("Find Sample", self)
        #need to resolve that these run sequentially. Maybe try threading again
        #locate_sample_button.clicked.connect(self.update_image_window)
        locate_sample_button.clicked.connect(self.locate_sample_action)


        cancel_button = QPushButton("Cancel", self)
        cancel_button.clicked.connect(self.cancel_program)

        acquire_full_angles_button = QPushButton("Acquire full angles", self)
        acquire_full_angles_button.clicked.connect(self.acquire_full_angles)

        duplicate_and_add_your_button = QPushButton("Duplicate and add your button", self)
        duplicate_and_add_your_button.clicked.connect(self.add_your_code)
        button_layout.addWidget(locate_sample_button)
        button_layout.addWidget(cancel_button)
        button_layout.addWidget(acquire_full_angles_button)
        button_layout.addWidget(duplicate_and_add_your_button)
        

        # Add the layouts in order, to build up the GUI
        vertical_layout.setAlignment(Qt.AlignTop)
        vertical_layout.addLayout(radio_layout)
        vertical_layout.addLayout(form_layout)
        vertical_layout.addLayout(button_layout)
        # Add the combined vertical layouts on the left, then the image on the right to the horiztonal "layout"
        layout.addLayout(vertical_layout)
        layout.addLayout(image_layout)

        central_widget = QWidget()
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)

    def create_field(self, label_text, default_value=None, is_string=False):
        field = QLineEdit(self)
        field.setAlignment(Qt.AlignRight)
        if default_value:
            field.setText(default_value)
        if not is_string:
            field.setValidator(QDoubleValidator())
        return field

    def create_buttons(self):
        pass

    def locate_sample_action(self):
        laser_value = self.get_selected_radio_value()
        power_percent_value = self.laser_power_field.text()
        z_depth_value = self.z_search_depth_field.text()
        data_storage = self.data_storage_field.text()
        #self.close()
        locate_sample_thread = Thread(target=locate_sample, args=(visualize_queue, visualize_event,other_data_queue, image_queue, command_queue, z_plane_queue, intensity_queue, view_snapshot, system_idle, processing_event, send_event, terminate_event, data0_queue, data1_queue, data2_queue, value_queue, stage_location_queue, laser_value, power_percent_value, z_depth_value, data_storage))
        locate_sample_thread.daemon = True
        locate_sample_thread.start()
        self.update_display_thread()
        #locate_sample(visualize_event,other_data_queue, image_queue, command_queue, z_plane_queue, intensity_queue, view_snapshot, system_idle, processing_event, send_event, terminate_event, data0_queue, data1_queue, data2_queue, value_queue, stage_location_queue, laser_value, power_percent_value, z_depth_value, data_storage)

    def get_selected_radio_value(self):
        for radio_button in self.radio_buttons:
            if radio_button.isChecked():
                return radio_button.text()

        return None
    
    def cancel_program(self):
        terminate_event.set()
        QApplication.instance().quit()
        self.close()

    def acquire_full_angles(self):
        # Open the PNG file
        image = Image.open('output_png/Brightfield_0.png')

        # Convert the image to a NumPy array
        image_array = np.array(image)

        self.display_image(image_array)
        # Perform the desired functionality with the obtained values
        # print("Laser:", laser_value)
        # print("Laser Power Percent:", power_percent_value)
        # print("Z Search Depth (mm):", z_depth_value)
        # print("USB Drive Name:", data_storage)
        pass
    def add_your_code(self):
        pass

if __name__ == "__main__":
    app = QApplication(sys.argv)
    #image_window = ImageWindow()
    controller = FlamingoController()

    controller.show()

    sys.exit(app.exec_())