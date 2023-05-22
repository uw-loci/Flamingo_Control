import sys
import os
import shutil
from PyQt5.QtWidgets import QApplication, QMainWindow, QLabel, QLineEdit, QPushButton 
from PyQt5.QtWidgets import QRadioButton, QFileDialog, QMessageBox, QWidget, QVBoxLayout, QFormLayout, QHBoxLayout
from PyQt5.QtGui import QDoubleValidator
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QMessageBox
from locate_sample import locate_sample
from queue import Queue
from threading import Event
import functions.text_file_parsing

# from PIL import Image
# from PyQt5.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget
# from PyQt5.QtGui import QPixmap, QImage, QColor
# from PyQt5.QtCore import Qt



#Set up some queues and events to keep track of what is happening across multiple threads
###################################
image_queue, command_queue, z_plane_queue, intensity_queue = Queue(), Queue(), Queue(), Queue()
view_snapshot, system_idle, processing_event, send_event, terminate_event = Event(), Event(), Event(), Event(), Event()
#, visualize_event = Event(),
#queues to send data with commands. Possibly condense this
data0_queue, data1_queue, data2_queue, value_queue = Queue(),Queue(),Queue(),Queue()
stage_location_queue = Queue()
######################################

#Need to:
# Handle the snapshot/get a snapshot workflow?
# Get 
class FlamingoController(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Flamingo Controller")
        self.setGeometry(200, 200, 500, 300)
        self.lasers = []
        self.selected_laser = ''
        self.laser_power = ''
        self.data_storage_location = ''
        # Check if 'ZStack.txt' file exists, otherwise prompt user to select a file
        self.check_zstack_file()
        
        # Create labels and fields
        self.create_fields()

        # Create buttons
        self.create_buttons()


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

                    #self.laser_power_string = 
                    #self.laser_type_string =[laser_channel] = str(laser_setting)
                except OSError as e:
                    QMessageBox.warning(self, "Error", str(e))
            else:
                QMessageBox.warning(self, "Warning", "ZStack.txt file not found! Closing.")
        #Read the text file to determine the default values for the dialog box
        zdict = text_file_parsing.workflow_to_dict(file_path)
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
        layout = QVBoxLayout()
        radio_layout = QHBoxLayout()

        # Get labels from a function (replace this with your own function)
        labels = self.get_radio_button_labels()
        self.radio_buttons = []
        for label in labels:
            radio_button = QRadioButton(label)
            if label == self.selected_laser:
                radio_button.setChecked(True)
            self.radio_buttons.append(radio_button)
            radio_layout.addWidget(radio_button)

        layout.addLayout(radio_layout)
        form_layout = QFormLayout()


        self.laser_power_field = self.create_field("Laser Power Percent", is_string=True, default_value=self.laser_power)
        self.z_search_depth_field = self.create_field("Z Search Depth (mm)", default_value="2.0")
        self.data_storage_field = self.create_field("Data storage path:", is_string=True, default_value=self.data_storage_location)

        form_layout.addRow("Laser Power Percent:", self.laser_power_field)
        form_layout.addRow("Z Search Depth (mm):", self.z_search_depth_field)
        form_layout.addRow("Data storage path:", self.data_storage_field)

        layout.addLayout(form_layout)

        button_layout = QVBoxLayout()
        locate_sample_button = QPushButton("Find Sample", self)
        locate_sample_button.clicked.connect(self.locate_sample_action)

        cancel_button = QPushButton("Cancel", self)
        cancel_button.clicked.connect(self.cancel_program)

        button_layout.addWidget(locate_sample_button)
        button_layout.addWidget(cancel_button)

        layout.addLayout(button_layout)

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

    def get_radio_button_labels(self):
        # Replace this with your own function to get the labels for the radio buttons
        return self.lasers

    def locate_sample_action(self):
        laser_value = self.get_selected_radio_value()
        power_percent_value = self.laser_power_field.text()
        z_depth_value = self.z_search_depth_field.text()
        data_storage = self.data_storage_field.text()
        self.close()
        locate_sample( image_queue, command_queue, z_plane_queue, intensity_queue, view_snapshot, system_idle, processing_event, send_event, terminate_event, data0_queue, data1_queue, data2_queue, value_queue, stage_location_queue, laser_value, power_percent_value, z_depth_value, data_storage)
        

    def get_selected_radio_value(self):
        for radio_button in self.radio_buttons:
            if radio_button.isChecked():
                return radio_button.text()

        return None
    
    def cancel_program(self):
        terminate_event.set()
        QApplication.instance().quit()

    def acquire_full_angles(self):
        laser_value = self.laser_field.text()
        power_percent_value = self.laser_power_field.text()
        z_depth_value = self.z_search_depth_field.text()
        data_storage = self.data_storage_field.text()

        # Perform the desired functionality with the obtained values
        # print("Laser:", laser_value)
        # print("Laser Power Percent:", power_percent_value)
        # print("Z Search Depth (mm):", z_depth_value)
        # print("USB Drive Name:", data_storage)
        pass

if __name__ == "__main__":
    app = QApplication(sys.argv)
    controller = FlamingoController()
    controller.show()

    sys.exit(app.exec_())