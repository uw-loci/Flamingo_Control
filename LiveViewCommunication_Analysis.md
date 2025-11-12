# LiveView Communication Analysis

## LiveView Application (Linux/LiveView)

### Purpose and Role in the System

The LiveView application is a standalone Linux-based client program that receives and displays real-time image data from camera workstations in the Flamingo Control system. It serves as a remote visualization tool, enabling operators to monitor live camera feeds from one or two cameras simultaneously, with support for advanced viewing modes including overlay, RGB merging, subtraction, zooming, and histogram display.

The application is built using OpenCV for image processing and display, and communicates with the camera server over TCP/IP sockets to receive streaming image data.

### Key Components and Architecture

#### Main Application Entry Point (main.cpp)

The main.cpp file provides a minimal bootstrap for the application:

```cpp
int main(int argc, char *argv[])
{
    SystemLog systemLog(SystemSupport::getExeName(argc, argv), true);
    SystemLog::logger()->info(SPD_TRACE("***** Application start *****"));

    LiveView liveView;
    liveView.setExePath(SystemSupport::getExePath(argc, argv));
    liveView.startLiveView();

    SystemLog::logger()->info(SPD_TRACE("***** Application terminated *****"));
    return 0;
}
```

The entry point initializes logging, creates a LiveView instance, sets the executable path (used for configuration file location), and starts the live view loop.

#### LiveView Class (LiveView.h/LiveView.cpp)

The LiveView class is the core component that handles network communication, image processing, and display management.

### Key Functions and Responsibilities

#### Connection Management

**`startLiveView()`** - Main execution loop that:
- Reads connection settings from LiveviewSettings.txt configuration file
- Attempts to connect to the server using the LiveViewControl class
- Implements automatic reconnection with a 2500ms timeout if connection is lost
- Continuously receives and displays image data while connected
- Monitors for user exit command ('x' or 'X' key)

**`getLiveViewSettings(int& liveViewConnectionPort)`** - Configuration loader that:
- Reads host name, IP address, and port number from LiveviewSettings.txt
- Validates port number is within range (53710-53810)
- Falls back to localhost:53718 if configuration file is not found
- Returns connection parameters for establishing server connection

The settings file format is: `hostname ip_address port_number`

#### Image Data Processing

**`storeImage(ImageData& imageData)`** - Primary image processing function that:
- Validates incoming image data buffer matches expected size
- Verifies pixel depth is 16-bit (2 bytes per pixel)
- Applies brightness/contrast adjustments using lookup tables
- Handles max projection mode when enabled
- Performs camera-specific transformations:
  - Camera 1: Vertical flip followed by 90-degree counterclockwise rotation
  - Camera 2: 90-degree counterclockwise rotation only
- Triggers histogram display for the received image
- Updates the appropriate DisplayImageData structure and sets update flag

**`calculateLookupTable16bits(ImageData& imageData)`** - Brightness/contrast adjustment:
- Creates a 16-bit lookup table (65536 entries) for fast pixel remapping
- Remaps pixel values from [imageScaleMin, imageScaleMax] range to full [0, 65535] range
- Clamps values below minimum to 0 and above maximum to 65535
- Only recalculates when scale parameters change to improve performance

**`storeImageCamera1And2(const ImageHeader& imageHeader)`** - Dual-camera processing:
- Combines images from both cameras when overlay modes are enabled
- Supports two combination modes:
  - **Subtract mode**: Computes absolute difference between camera images
  - **RGB mode**: Merges images into color channels (Camera 1 → green, Camera 2 → red, blue → black)
- Ensures both images have matching dimensions before processing
- Updates the overlay DisplayImageData structure

#### Display Management

**`displayCameraImage(DisplayImageData& displayImageData)`** - Comprehensive display function that:
- Handles split overlay mode (splits image vertically, displays left half in green, right half in red)
- Renders crosshair overlay when enabled (black outline with white center for visibility)
- Implements zoom functionality by extracting and upscaling ROI
- Creates or reuses OpenCV windows as needed
- Automatically sizes and positions windows based on screen resolution
- Accounts for platform-specific UI elements (Linux menu/taskbar height: 103 pixels)
- Displays zoom level indicator text when zoomed
- Shows the processed image and resets display flags

**`displayHistogram(cv::Mat& sourceImage, uint32_t deviceIndex)`** - Histogram visualization:
- Generates 256-bin histogram for 16-bit image data
- Creates both linear and logarithmic histogram visualizations
- Linear histogram shown in dark red (0, 0, 128)
- Logarithmic histogram shown in green (0, 128, 0)
- Displays gray vertical lines marking the histogram data range boundaries
- Window titled as "Histogram_[hostname]_C[camera_number]"

#### Zoom and View Control

**`setUserImageOptions(const ImageHeader& imageHeader)`** - Processes viewing commands from server:
- Detects zoom enable flags for each camera view
- Determines which camera(s) to apply zoom settings to
- Handles split overlay mode activation
- Delegates to setUserImageOptionsDisplay for detailed zoom calculations

**`setUserImageOptionsDisplay(const ImageHeader& imageHeader, DisplayImageData& displayImageData)`** - Calculates zoom region:
- Extracts sector IDs from image header (1-9, representing 3x3 grid)
- Recursively divides image into sectors to determine ROI
- Sector numbering scheme:
  ```
  1 2 3
  4 5 6
  7 8 9
  ```
- Supports two-level zoom (sector within sector) for 9x9 grid granularity
- Extracts crosshair coordinates when crosshair mode is enabled
- Sets zoom and crosshair flags for display function

**`zoomIn(DisplayImageData& displayImageData, int x, int y)`** - Client-side zoom (currently unused):
- Calculates ROI centered on mouse coordinates
- Clamps ROI boundaries to image dimensions
- Enables zoom and crosshair display flags

**`zoomOut(DisplayImageData& zoomData, int x, int y)`** - Disables zoom mode

### Data Structures

#### DisplayImageData Structure

This structure maintains the complete state for each camera view window:

```cpp
struct DisplayImageData
{
    std::string windowName;          // OpenCV window identifier
    int mouseSelectX;                // Mouse/crosshair X coordinate
    int mouseSelectY;                // Mouse/crosshair Y coordinate
    int zoomSize;                    // Zoom window size in pixels
    std::atomic_bool zoomEnabled;    // Zoom mode active flag
    std::atomic_bool updateImage;    // Image needs redisplay flag
    std::atomic_bool splitOverlay;   // Split overlay mode flag
    bool initialImage;               // First image flag
    bool crossHairEnabled;           // Crosshair overlay flag
    int moveWindowX;                 // Initial window X position
    int moveWindowY;                 // Initial window Y position
    int imageWidth;                  // Current display width
    int imageHeight;                 // Current display height
    cv::Rect roi;                    // Region of interest for zoom
    cv::Mat imageOriginal;           // Original received image
    cv::Mat imageProcessing;         // Intermediate processing buffer
    cv::Mat imageDisplay;            // Final display image
};
```

The application maintains three static instances:
- `m_displayImageDataCamera1` - Camera 1 view
- `m_displayImageDataCamera2` - Camera 2 view
- `m_displayImageDataCameras1And2` - Overlay/combined view

### Important Constants and Enums

#### Network Configuration

```cpp
const int CONNECTION_PORT_RANGE_START = 53710;
const int CONNECTION_PORT_RANGE_END = 53810;
const long long CONNECTION_RETRY_TIMEOUT_MS = 2500;
```

Default fallback: `localhost:53718`

#### Zoom Levels

```cpp
enum ZOOM {
    ZOOM_16 = 0, ZOOM_32, ZOOM_64, ZOOM_128,
    ZOOM_256, ZOOM_512, ZOOM_1024, ZOOM_MAX
};
int m_zoomSize[ZOOM_MAX] = { 16, 32, 64, 128, 256, 512, 1024 };
```

Default zoom size: `ZOOM_256` (256 pixels)

#### Window Names

```cpp
#define CAMERA1_WINDOW              "Camera1"
#define CAMERA2_WINDOW              "Camera2"
#define CAMERAS_1_AND_2_WINDOW      "Overlay"
```

Runtime window names: `Preview_[hostname]_C1`, `Preview_[hostname]_C2`, `Overlay_[hostname]`

#### Array Size

```cpp
enum { ARRAY_SIZE_16_BIT = 65536 };
```

Used for 16-bit lookup table allocation.

### Communication Protocol

The LiveView application acts as a **TCP client** that connects to a camera server. Communication is managed through the `LiveViewControl` class (not shown in these files, but referenced).

#### Connection Flow

1. Application reads configuration from LiveviewSettings.txt
2. Calls `liveViewControl.connectToServer(m_hostIPAddress, liveViewPortNumber)`
3. On successful connection, enters main receive/display loop
4. Continuously calls `liveViewControl.getImageData(imageData)` to fetch new frames
5. Monitors `liveViewControl.getConnectionStatus()` for connection health
6. On disconnect, waits 2500ms and attempts reconnection

#### Message Format

The application receives `ImageData` structures containing:
- **ImageHeader**: Metadata including device index, dimensions, scale parameters, and viewing options
- **dataBuffer**: Raw 16-bit pixel data

The ImageHeader encodes viewing parameters through bitfield methods:
- `optionZoomEnabled()` - Zoom mode active
- `optionZoomCamera1Enabled()` - Apply zoom to camera 1
- `optionZoomCamera2Enabled()` - Apply zoom to camera 2
- `optionZoomCameras1And2Enabled()` - Apply zoom to overlay view
- `optionZoomSector1Get()` / `optionZoomSector2Get()` - Sector coordinates (1-9)
- `optionCrossHairEnabled()` - Crosshair overlay active
- `optionCrossHairXGet()` / `optionCrossHairYGet()` - Crosshair coordinates
- `optionImageSplitOverlayEnabled()` - Split overlay mode
- `optionSubtractEnabled()` - Subtraction overlay mode
- `optionsRGBEnabled()` - RGB merge overlay mode

### How It Orchestrates Live View Functionality

The application follows a polling-based architecture:

1. **Initialization Phase**:
   - Loads configuration settings
   - Determines screen resolution for optimal window sizing
   - Initializes display data structures with default zoom levels and window positions

2. **Connection Phase**:
   - Attempts server connection
   - Creates dynamic window names based on hostname
   - Enters active display loop on success

3. **Display Loop** (while connected):
   - **Receive**: Calls `getImageData()` to fetch next frame (non-blocking)
   - **Process**: If new data received, calls `storeImage()` to process and transform
   - **Configure**: Applies viewing options from image header via `setUserImageOptions()`
   - **Display**: Updates all three camera windows as needed
   - **Refresh**: Calls `cv::waitKey(1)` to process OpenCV events and allow 1ms for rendering
   - **Monitor**: Checks connection status and user exit request

4. **Reconnection Phase**:
   - On disconnect, closes windows and waits 2500ms
   - Returns to connection phase
   - Repeats until user presses 'x' or 'X'

5. **Shutdown**:
   - Calls `disconnectFromServer()`
   - Logs termination message
   - OpenCV windows automatically close on application exit

### Platform Considerations

The code includes platform-specific handling:

- **Linux**: Accounts for 103-pixel UI overhead (menu + taskbar) when sizing windows
- **macOS**: Uses image resizing instead of window resizing
- **Cross-platform**: Uses OpenCV's cross-platform window management

The split overlay feature performs camera orientation corrections, suggesting the cameras may be physically mounted at different angles.

### Summary

The LiveView application is a robust, feature-rich visualization client designed for real-time monitoring of dual-camera microscopy systems. It provides sophisticated image processing capabilities including brightness/contrast adjustment, multi-camera overlays, zoom with sector-based navigation, crosshair positioning, and histogram analysis. The architecture cleanly separates network communication, image processing, and display concerns while maintaining high performance through lookup table optimization and atomic flag-based update signaling.

## LiveViewControl (Shared/LiveView)

### Purpose and Role in the System

The `LiveViewControl` class serves as a client-side component responsible for receiving and managing real-time image data streams from a server. It acts as a TCP/IP client specifically designed to handle continuous live video feeds from multiple imaging devices (cameras). The class extends `TCPIPClient` to provide specialized functionality for receiving, buffering, and distributing image frames in a multi-threaded environment.

Key responsibilities include:
- Establishing and maintaining TCP/IP connections to the live view server
- Receiving image data in a dedicated background thread
- Managing separate image queues for multiple devices (up to 4 devices)
- Providing thread-safe access to received image data
- Handling connection lifecycle and graceful shutdown

### Key Functions and Their Responsibilities

#### Connection Management

**`bool connectToServer(const std::string& host, int portNumber)`**
- Establishes TCP/IP connection to the live view server
- Spawns a dedicated receive thread (`recvLiveViewDataThread`) upon successful connection
- Returns true if connection is successful and receive thread is started
- Implementation:
```cpp
if (TCPIPClient::connectToServer(host, portNumber) == true)
{
    if (false == m_liveviewThreadRun)
    {
        terminateThread();
        m_liveviewThreadRun = true;
        m_liveviewThread = std::thread(&LiveViewControl::recvLiveViewDataThread, this);
    }
    retStatus = true;
}
```

**`void disconnectFromServer()`**
- Terminates the receive thread
- Closes the TCP/IP connection
- Ensures clean shutdown of all resources

**`void terminateThread()`**
- Safely stops the receive thread by setting `m_liveviewThreadRun` to false
- Joins the thread to ensure it has fully terminated before returning

#### Data Reception and Processing

**`void recvLiveViewDataThread()`**
- Core worker thread that continuously receives image data from the server
- Uses `select()` with 500ms timeout for non-blocking socket monitoring
- Implements a two-stage receive protocol:
  1. First reads the `ImageHeader` structure (fixed size)
  2. Then reads the variable-size image data buffer based on `imageHeader.imageSize`
- Tracks statistics including total images received and frame timing
- Handles partial reads with retry logic and timeout protection (2000ms for image data)
- Key implementation details:
```cpp
// Read header first
bytesRead = TCPIPClient::readData((void*) &imageData.imageHeader,
                                  sizeof(imageData.imageHeader),
                                  IOREADWRITE_TIMEOUT_MS);

// Then read image data in chunks
while (0 < bytesLeftToRead && m_liveviewThreadRun)
{
    bytesRead = TCPIPClient::readData((void*) &imageData.dataBuffer[totalBytesRead],
                                      bytesLeftToRead,
                                      READ_IMAGE_DATA_TIMEOUT_MS);
    bytesLeftToRead -= bytesRead;
    totalBytesRead += bytesRead;
}
```

**`bool getImageData(ImageData& imageData)`**
- Thread-safe method for extracting received images from the buffer queues
- Implements round-robin extraction across all device queues
- Uses mutex protection to prevent race conditions
- Returns true if an image was successfully retrieved, false otherwise
- Implementation uses modulo indexing to cycle through device queues:
```cpp
std::lock_guard<std::mutex> guard(m_liveviewThreadMutex);
retValue = m_vImageDataList[m_imageExtractionIndex].getFront(imageData);
++m_imageExtractionIndex;
```

**`void push_back(const ImageData& imageData)`**
- Internal method to add received images to the appropriate device queue
- Routes images based on `deviceIndex` in the header
- Validates device index before inserting

### Data Structures and Message Formats

#### ImageHeader Structure
A comprehensive 40-byte header (10 uint32_t fields) that describes each image frame:

```cpp
struct ImageHeader {
    uint32_t imageSize;          // Size of image data in bytes
    uint32_t imageWidth;         // Image width in pixels
    uint32_t imageHeight;        // Image height in pixels
    uint32_t imageScaleMin;      // Minimum scale value for display
    uint32_t imageScaleMax;      // Maximum scale value for display
    uint32_t deviceIndex;        // Camera/device identifier (0-3)
    uint32_t optionsSettings1;   // Bitfield for image options (see IMAGE_OPTION enum)
    uint32_t optionsSettings2;   // Additional options bitfield
    int32_t imageIndexStart;     // Starting frame index
    int32_t imageIndexStop;      // Ending frame index
};
```

The header provides rich functionality through bitfield operations for:
- Image processing modes (RGB, Subtract)
- UI overlays (cross-hair, zoom, split view)
- Multi-camera configurations
- Frame range information

#### IMAGE_OPTION Enum
Bitfield values stored in optionsSettings1 and optionsSettings2:

```cpp
typedef enum IMAGE_OPTION
{
    IMAGE_OPTION_NONE                   = 0x00000000,
    IMAGE_OPTION_SUBTRACT               = 0x00000001,
    IMAGE_OPTION_RGB                    = 0x00000002,
    IMAGE_OPTION_CROSS_HAIR_ENABLE      = 0x00000004,
    IMAGE_OPTION_ZOOM_ENABLE            = 0x00000008,
    IMAGE_OPTION_IMAGE_DATA             = 0x00000010,
    IMAGE_OPTION_IMAGE_MAX_PROJECTION   = 0x00000020,
    IMAGE_OPTION_SPLIT_OVERLAY          = 0x00000040,
    IMAGE_OPTION_ZOOM_CAMERA_1_MASK     = 0x00000100,
    IMAGE_OPTION_ZOOM_CAMERA_2_MASK     = 0x00000200,
    IMAGE_OPTION_ZOOM_CAMERAS_1_2_MASK  = 0x00000400,
    IMAGE_OPTION_ZOOM_SECTOR_1_MASK     = 0x00FF0000,
    IMAGE_OPTION_ZOOM_SECTOR_2_MASK     = 0xFF000000,
    IMAGE_OPTION_CROSS_HAIR_X_MASK      = 0xFFFF0000,
    IMAGE_OPTION_CROSS_HAIR_Y_MASK      = 0x0000FFFF,
} IMAGE_OPTION;
```

#### ImageData Structure
Complete image frame package combining header and payload:

```cpp
struct ImageData {
    ImageHeader imageHeader;              // Frame metadata
    std::vector<unsigned char> dataBuffer; // Raw image bytes
};
```

#### ImageDataList Class
Thread-safe FIFO queue with automatic overflow management:
- Default maximum size: 10 frames per device
- Uses `std::list<ImageData>` as underlying container
- Mutex-protected push/pop operations
- Automatically drops oldest frames when queue is full
- Provides `pushBack()` and `getFront()` methods

Implementation details:
```cpp
void pushBack(const ImageData& imageData)
{
    std::lock_guard<std::mutex> guard(m_imageListMutex);
    m_imageList.push_back(imageData);
    if (m_imageList.size() > m_imageListMaxSize)
        m_imageList.pop_front();  // Drop oldest frame
}

bool getFront(ImageData& imageData)
{
    std::lock_guard<std::mutex> guard(m_imageListMutex);
    if (0 < m_imageList.size())
    {
        imageData = m_imageList.front();
        m_imageList.pop_front();
        return true;
    }
    return false;
}
```

### Important Constants and Configuration

**Timeout Values:**
```cpp
const long long IOREADWRITE_TIMEOUT_MS = 100;      // Header read timeout
const long long READ_IMAGE_DATA_TIMEOUT_MS = 2000; // Image data read timeout
const long long SELECT_TIMEOUT_MS = 500;           // Socket select timeout (defined in thread)
```

**Buffer Configuration:**
```cpp
const ssize_t MAX_VECTOR_SIZE = 5;  // Maximum device queue size
```

**Device Configuration:**
- Supports up to 4 simultaneous imaging devices (`m_vImageDataList.resize(4)`)
- Each device maintains its own independent image queue
- Round-robin extraction ensures fair access to all device streams

### Communication Protocol

The LiveViewControl implements a binary protocol over TCP/IP:

#### Transmission Format
1. **Header Phase**: Fixed 40-byte ImageHeader structure sent first
2. **Data Phase**: Variable-length image buffer (size specified in header)

#### Flow Control
- Server continuously pushes frames without client acknowledgment (streaming protocol)
- Client uses `select()` to monitor socket availability before reading
- Partial frame reads are accumulated until complete frame is received
- Network interruptions detected through read failures or incorrect byte counts

#### Error Handling
- Connection loss detected when header read returns incorrect size
- Partial data reads logged with progress information for debugging
- Thread terminates gracefully on connection failure or select error
- Image counter tracks total frames successfully received during session

#### Receive Loop Logic
```cpp
while (true == m_liveviewThreadRun)
{
    // Wait for data availability with timeout
    selectStatus = select(sockfd + 1, &read_fds, NULL, NULL, &tv);

    if (selectStatus > 0)
    {
        // Read fixed-size header
        bytesRead = readData(&imageData.imageHeader,
                            sizeof(imageData.imageHeader),
                            IOREADWRITE_TIMEOUT_MS);

        if (bytesRead == sizeof(imageData.imageHeader))
        {
            // Resize buffer for image data
            imageData.dataBuffer.resize(imageData.imageHeader.imageSize);

            // Read variable-size image data
            while (bytesLeftToRead > 0)
            {
                bytesRead = readData(&imageData.dataBuffer[totalBytesRead],
                                    bytesLeftToRead,
                                    READ_IMAGE_DATA_TIMEOUT_MS);
                totalBytesRead += bytesRead;
                bytesLeftToRead -= bytesRead;
            }

            // Add complete frame to appropriate device queue
            push_back(imageData);
        }
        else
        {
            // Connection closed by server
            break;
        }
    }
}
```

### Thread Safety and Synchronization

**Concurrency Model:**
- Single dedicated receive thread runs continuously while connected
- Main thread(s) call `getImageData()` to extract frames
- Mutex (`m_liveviewThreadMutex`) protects the shared image queue vector
- Atomic boolean (`m_liveviewThreadRun`) controls thread lifecycle

**Lock-Free Communication:**
- Uses `std::atomic_bool` for thread control flag (no lock required for reads/writes)
- Minimizes critical sections through per-device queue design
- Round-robin extraction avoids contention on individual queues
- Each `ImageDataList` has its own internal mutex for fine-grained locking

**Synchronization Guarantees:**
- Thread-safe image insertion (receive thread only)
- Thread-safe image extraction (consumer threads only)
- No race conditions between queue operations
- Clean thread shutdown guaranteed through join operation

### How It Communicates with Other Components

#### Integration Points

**Depends On:**
- `TCPIPClient`: Base class providing TCP/IP connection and I/O primitives
  - `connectToServer()`, `closeConnection()`, `getSockfd()`
  - `readData()` for blocking reads with timeout support
- `CameraInc.h`: Defines ImageData, ImageHeader, and ImageDataList structures
- `SystemLog`: Logging infrastructure for diagnostics and debugging
- `SystemSupport`: Utility functions (e.g., `msToTimeval()` for timeout conversions)

**Used By:**
- LiveView application (client applications requiring real-time camera feeds)
- UI components displaying live imaging data
- Recording/processing modules that consume image streams

**Connection Architecture:**
```
Application Layer
      |
      | getImageData()
      v
LiveViewControl (this class)
      |
      | readData(), connectToServer()
      v
TCPIPClient (base class)
      |
      | POSIX socket API
      v
Network Layer
      |
      | TCP/IP connection
      v
LiveView Server
```

**Multi-Device Data Flow:**
```
Server → Network → TCPIPClient → recvLiveViewDataThread()
                                         |
                                         v
                    push_back() → m_vImageDataList[deviceIndex]
                                         |
                    +--------------------+--------------------+
                    |                    |                    |
                    v                    v                    v
            [Device 0 Queue]     [Device 1 Queue]     [Device 2 Queue]
                    |                    |                    |
                    +--------------------+--------------------+
                                         |
                                         v
                              getImageData() (round-robin)
                                         |
                                         v
                                  Application
```

### Design Patterns and Best Practices

1. **Producer-Consumer Pattern**: Receive thread produces images, application threads consume them through bounded queues

2. **RAII (Resource Acquisition Is Initialization)**: Thread cleanup in destructor ensures proper shutdown even if exceptions occur

3. **Separation of Concerns**: Network I/O separated from image buffering logic, clean abstraction layers

4. **Defensive Programming**:
   - Validates device indices before array access
   - Handles partial reads with chunked approach
   - Timeout protection prevents indefinite blocking
   - Checks connection status before operations

5. **Scalability**: Multi-device support through vectorized queue architecture allows independent streams per device

6. **Diagnostic Logging**: Comprehensive logging for:
   - Thread lifecycle events (start, stop)
   - Connection status changes
   - Frame reception statistics
   - Partial read progress for debugging network issues

7. **Non-Blocking I/O**: Uses `select()` to avoid blocking main receive loop when no data available

8. **Lock Coarsening**: Single mutex protects entire queue vector rather than individual operations for simplicity while maintaining acceptable performance

---

## InterfaceControl (Shared/LiveView)

### Overview and Purpose

The `InterfaceControl` class serves as the primary client-side communication interface for the Flamingo microscope control system's Live View functionality. It manages TCP/IP-based bidirectional communication between the client (Live Viewer application) and the server (microscope control system), handling both command transmission and data reception in a thread-safe, asynchronous manner.

**Key Responsibilities:**
- Establish and maintain TCP/IP connections to the microscope control server
- Send commands and file data to the server
- Asynchronously receive and process data from the server
- Provide callback mechanisms for connection events and data availability
- Manage connection lifecycle including error handling and graceful disconnection

### Architecture and Design Patterns

**Inheritance Hierarchy:**
- `InterfaceControl` inherits from `TCPIPClient` (protected inheritance)
- `TCPIPClient` provides low-level socket communication primitives
- This design encapsulates TCP/IP details while exposing domain-specific interface

**Multi-Threading Model:**
The class employs a dual-threaded architecture for handling asynchronous I/O:

1. **Read Data Thread** (`readDataThread`): Continuously reads incoming data from the TCP/IP socket
2. **Process Data Thread** (`processDataThread`): Processes received data and triggers callbacks

This separation ensures that I/O operations don't block data processing, maintaining system responsiveness.

**Thread Synchronization:**
- Uses `std::atomic_bool` for thread control flags
- Employs `std::promise` and `std::future` for thread initialization synchronization
- Thread-safe data queue (`DataBufferList`) with mutex protection

### Core Data Structures

#### SCommand Structure
The fundamental command structure used throughout the system (defined in `SystemCommands.h`):

```cpp
struct SCommand {
    uint32_t    cmdStart;              // Command start marker
    uint32_t    cmd;                   // Command code
    int32_t     status;                // Status/result code
    int32_t     hardwareID;            // Target hardware identifier
    int32_t     subsystemID;           // Subsystem identifier
    int32_t     clientID;              // Client identifier
    int32_t     int32Data0;            // Generic integer data field
    int32_t     int32Data1;            // Generic integer data field
    int32_t     int32Data2;            // Generic integer data field
    int32_t     cmdDataBits0;          // Bit flags for command options
    double      doubleData;            // Generic double precision data
    int32_t     additionalDataBytes;   // Size of additional payload data
    char        buffer[72];            // General purpose buffer
    uint32_t    cmdEnd;                // Command end marker
};
```

**Structure Size:** Fixed size with start/end markers for validation
**Additional Data:** Commands can carry variable-length payloads via `additionalDataBytes`

**Command Data Bits (Flags):**
The `cmdDataBits0` field uses bit flags for various command options:
- `COMMAND_DATA_BITS_TRIGGER_CALL_BACK`: Request callback on completion
- `COMMAND_DATA_BITS_EXPERIMENT_TIME_REMAINING`: Include time remaining info
- `COMMAND_DATA_BITS_STAGE_POSITIONS_IN_BUFFER`: Stage position data included
- `COMMAND_DATA_BITS_MAX_PROJECTION`: Maximum intensity projection requested
- `COMMAND_DATA_BITS_SAVE_TO_DISK`: Save data to disk flag
- `COMMAND_DATA_BITS_STAGE_NOT_UPDATE_CLIENT`: Suppress client update
- `COMMAND_DATA_BITS_STAGE_ZSWEEP`: Z-axis sweep operation

#### SDataBuffer Class
Wrapper class for serializing/deserializing command data:

```cpp
class SDataBuffer {
public:
    std::vector<char> m_vBuffer;

    bool toBuffer(const SCommand& command);
    bool toBuffer(const SCommand& command, const std::string& additionalData);
    bool fromBuffer(SCommand& command) const;
    bool fromBuffer(SCommand& command, std::string& additionalData) const;
    // Additional overloads for vector<char> data
};
```

**Purpose:** Provides serialization layer for network transmission

#### DataBufferList Class
Thread-safe queue for buffering incoming data:

```cpp
class DataBufferList {
private:
    std::list<SDataBuffer>    m_bufferList;
    std::mutex                m_bufferListMutex;

public:
    void pushBack(const SDataBuffer& buffer);  // Producer (read thread)
    bool getFront(SDataBuffer& buffer);        // Consumer (process thread)
};
```

**Pattern:** Producer-Consumer queue with mutex protection
**Usage:** Decouples network I/O from data processing

### Key Functions and Operations

#### Connection Management

**connectToServer()**
```cpp
bool connectToServer(const std::string& host, int portNumber)
```
- Establishes TCP/IP connection to server
- Initializes and starts both read and process threads
- Uses promise/future pattern to ensure threads start successfully
- Returns `true` on successful connection and thread initialization
- Prevents multiple simultaneous connections

**closeConnection()**
```cpp
void closeConnection()
```
- Signals threads to terminate via `m_threadRun` flag
- Waits for both threads to join gracefully
- Closes underlying TCP/IP connection
- Called automatically in destructor

#### Command Transmission

**sendScopeCommand()**
```cpp
bool sendScopeCommand(SCommand& scmd, std::string additionalData = "")
```
- Sends a command structure with optional additional data payload
- Sets client ID automatically before transmission
- Serializes command using `SDataBuffer::toBuffer()`
- Performs atomic write operation with timeout (1000ms default)
- Detects connection closure and sets `m_serverConnectionLost` flag
- Returns `true` if entire buffer transmitted successfully

**Example Command Flow:**
1. Populate `SCommand` structure with command code and parameters
2. Optionally include additional data (JSON, binary data, etc.)
3. Call `sendScopeCommand()`
4. Method serializes to buffer and transmits
5. Server processes and may respond asynchronously

**sendScopeFile()**
```cpp
bool sendScopeFile(SCommand& scmd, std::string& dataFilePath)
```
- Specialized method for transmitting file data to server
- Reads entire file into memory (with size validation)
- Sends command structure first, then file data
- Uses extended timeout (15 seconds) for large file transfers
- Enforces maximum file size: `ID_SYSTEM_UPDATE_MAX_DATA_SIZE_BYTES`
- Use case: System updates, firmware uploads

**File Transfer Protocol:**
1. Open and read file into buffer
2. Set `scmd.additionalDataBytes` to file size
3. Send `SCommand` structure
4. Send file data buffer
5. Server receives both parts and processes

#### Data Reception and Processing

**readDataThread()** (protected)
```cpp
void readDataThread(std::promise<bool>& threadStartStatus, std::atomic_bool& threadRun)
```
- Continuous loop reading from socket
- Reads `SCommand` structures (fixed size)
- If `additionalDataBytes > 0`, reads additional payload
- Packages data into `SDataBuffer` and queues to `DataBufferList`
- Uses shorter timeout (200ms) for responsive shutdown
- Detects connection loss and sets flag

**Read Operation Flow:**
```
1. readDataBuffer() - Read SCommand structure
2. Check scmd.additionalDataBytes
3. If > 0: readData() for additional payload
4. Serialize to SDataBuffer
5. m_dataBufferList.pushBack()
6. Loop continues
```

**processDataThread()** (protected)
```cpp
void processDataThread(std::promise<bool>& threadStartStatus, std::atomic_bool& threadRuns)
```
- Dequeues buffers from `DataBufferList`
- Calls `processDataBuffer()` for each buffer
- Sleeps briefly (100 microseconds) when queue empty
- Continues until `threadRuns` flag cleared

**processDataBuffer()** (protected, virtual)
```cpp
void processDataBuffer(const SDataBuffer& dataBuffer)
```
- Triggers registered callbacks with data buffer
- Base implementation: calls `m_callbackScopeDataAvailable`
- Virtual: allows derived classes to override for custom processing

**readDataTerminate()** (protected, virtual)
```cpp
void readDataTerminate()
```
- Called when read thread exits
- If connection lost (not graceful close), triggers disconnect callback
- Ensures client is notified of unexpected disconnections
- Closes underlying TCP/IP connection

### Communication Protocol

#### Message Format

**Standard Message:**
```
[SCommand Structure (132 bytes)] + [Optional Additional Data (variable length)]
```

**SCommand Layout:**
- Fixed 132-byte structure (platform-dependent, includes padding)
- Start marker: `cmdStart` = `0xF321E654`
- End marker: `cmdEnd` = `0xFEDC4321`
- These markers provide basic validation

**Serialization:**
- Binary serialization (no text encoding)
- Native endianness (assumes same architecture for client/server)
- Additional data follows command structure immediately

#### Command Codes

Commands are categorized by subsystem (from `CommandCodes.h`):

**Common Commands** (`0x00001000` range):
- System configuration
- Settings save/load
- Power management
- System updates

**Camera Commands** (`0x00003000` range):
- `COMMAND_CODES_CAMERA_LIVE_VIEW_START/STOP`
- `COMMAND_CODES_CAMERA_SNAPSHOT_GET`
- `COMMAND_CODES_CAMERA_WORK_FLOW_START/STOP`
- Camera settings (exposure, ROI, trigger mode, FPS)
- Image processing options (max projection, save formats)

**Laser Commands** (`0x00002000` range):
- Laser enable/disable
- Power level control
- Recording mode

**Other Subsystems:**
- Stage commands
- LED illumination
- Filter wheel
- UI updates
- Metadata

### Callback Mechanism

The class uses a callback pattern for asynchronous event notification:

**Connection Lost Event:**
```cpp
template<typename T_object>
void RegisterConnectionLostEvent(T_object* object,
                                 void(T_object::*function)(void*, void*),
                                 void* userData)
```
- Notified when server connection drops unexpectedly
- Allows client to update UI, attempt reconnection, etc.

**Scope Data Available Event:**
```cpp
template<typename T_object>
void RegisterScopeDataEvent(T_object* object,
                           void(T_object::*function)(void*, void*),
                           void* userData)
```
- Notified when data received from server
- Passes `SDataBuffer` pointer to callback
- Client parses buffer and processes (e.g., display image, update status)

**Callback Implementation:**
- Uses `CallbackHandler` class template-based registration
- Supports member function callbacks with user data
- Multiple callbacks can be registered

### Constants and Configuration

**Timeout Values:**
```cpp
const long long IOREADWRITE_TIMEOUT_MS = 1000;  // Standard I/O timeout
const long long readCommandTimeoutMS = 200;      // Read thread timeout
```

**Client Identification:**
- Each client assigned unique ID via `setClientID()`
- ID automatically inserted into all outgoing commands
- Server uses ID to route responses and manage multiple clients

### Error Handling and Connection Management

**Connection Loss Detection:**
- `iowrite()` returns `IOREADWRITE_CONNECTION_CLOSED` on disconnect
- `readData()` returns `IOREADWRITE_CONNECTION_CLOSED` on disconnect
- Sets `m_serverConnectionLost` atomic flag
- Triggers graceful thread shutdown

**Thread Safety:**
- All shared data protected by mutexes or atomics
- Thread lifecycle carefully managed with join operations
- Promise/future ensures threads start before use

**Graceful Shutdown:**
1. `closeConnection()` or destructor called
2. `m_threadRun` set to `false`
3. Read thread detects flag, exits loop
4. Process thread detects flag, exits loop
5. Both threads joined
6. TCP/IP socket closed

### Integration with System

**Upstream Dependencies:**
- `TCPIPClient`: Low-level socket I/O
- `CallbackHandler`: Event notification system
- `SystemCommands.h`: Command definitions and data structures
- `SystemLog`: Logging infrastructure

**Usage Pattern (Typical Client):**
```cpp
// 1. Create instance
InterfaceControl interface;

// 2. Register callbacks
interface.RegisterScopeDataEvent(this, &MyClass::onDataReceived, nullptr);
interface.RegisterConnectionLostEvent(this, &MyClass::onDisconnect, nullptr);

// 3. Connect to server
interface.connectToServer("192.168.1.100", 8080);
interface.setClientID(12345);

// 4. Send commands
SCommand cmd(COMMAND_CODES_CAMERA_LIVE_VIEW_START);
interface.sendScopeCommand(cmd);

// 5. Receive data via callbacks
void MyClass::onDataReceived(void* dataPtr, void* userData) {
    SDataBuffer* buffer = (SDataBuffer*)dataPtr;
    SCommand cmd;
    std::string additionalData;
    buffer->fromBuffer(cmd, additionalData);
    // Process command and data
}

// 6. Cleanup
interface.closeConnection();
```

### Design Strengths

1. **Separation of Concerns:** Network I/O separated from data processing
2. **Thread Safety:** Proper synchronization primitives throughout
3. **Extensibility:** Virtual methods allow customization in derived classes
4. **Callback Pattern:** Decouples communication from application logic
5. **Robust Error Handling:** Connection loss detection and graceful degradation
6. **Timeout Management:** Prevents indefinite blocking on I/O operations

### Potential Considerations

1. **Endianness:** Binary protocol assumes same-endian client/server
2. **Buffer Unlimited Growth:** `DataBufferList` has no size limit (could grow unbounded)
3. **Single Server:** Only supports one server connection at a time
4. **Synchronous Sends:** Command sending is synchronous (blocks until complete)
5. **Memory Usage:** Large files loaded entirely into memory before sending

### Summary

The `InterfaceControl` class provides a robust, thread-safe client-side communication layer for the Live View system. It abstracts TCP/IP complexity while providing a clean, callback-based interface for command transmission and data reception. The dual-threaded architecture ensures responsive I/O handling, while the structured command protocol enables flexible, extensible communication between the microscope control system and client applications. This design supports real-time live viewing, command execution, and bidirectional data flow essential for interactive microscope control.

## ControlSystem (Linux/ControlSystem)

### Overview of Architecture and Role

The ControlSystem is the server-side control application that manages the entire microscope hardware ecosystem, including cameras, stages, lasers, illumination systems, filter wheels, and LEDs. It serves as the central orchestration layer that receives commands from clients, coordinates hardware operations, executes acquisition workflows, and streams live image data to connected viewers. The system is built as a multi-threaded TCP/IP server application using a modular subsystem architecture with callback-based communication between components.

### Relevant Files for Live View Functionality

#### Core Control and Orchestration

**main.cpp** - Application entry point that:
- Initializes the system-wide logging infrastructure
- Creates the SystemControl object that manages all hardware subsystems
- Instantiates the ControlServer that handles client connections
- Starts the TCP/IP server listening on port ID_CONTROL_SERVER_CONNECTION_PORT
- Waits for user termination command

**ControlServer/ControlServer.h and .cpp** - Central command dispatcher and state manager:
- Inherits from TCPIPServer to handle multiple client connections
- Processes incoming SCommand structures from clients and routes them to appropriate subsystems
- Manages system state transitions (IDLE, LIVE_VIEW, SNAP_SHOT, WORK_FLOW_RUNNING, etc.)
- Implements callback registration system for receiving data from subsystems
- Coordinates live view "update on change" feature for responsive UI updates
- Maintains client notification lists for broadcasting system state changes
- Handles connection limits (ID_CONTROL_SERVER_CONNECTIONS_MAX)

**SystemInit/SystemControl.h and .cpp** - Hardware subsystem manager:
- Discovers and initializes all connected hardware devices (cameras, stages, lasers, etc.)
- Maintains collections of subsystem control objects (m_vpControlCameras, m_vpControlStages, etc.)
- Provides template-based callback registration for subsystem data availability
- Routes commands to appropriate subsystems via exeCommand() method
- Manages system-wide settings through SystemSettingsScope and SystemSettingsControl
- Supports multiple PCO camera instances (Panda and Edge models)

#### Camera Control Subsystem

**Subsystems/Camera/CameraControl.h** - Abstract camera interface defining:
- `imageCaptureComplete()` - Check if acquisition is finished
- `imageCaptureInit()` - Prepare camera for new acquisition
- `liveviewRunning()` - Query live view state
- `stackTake(WorkflowDataStack&)` - Execute workflow-based stack acquisition
- `getCameraPixelSize()` - Return physical pixel dimensions
- Access to MetaData object for experiment metadata

**Subsystems/Camera/BaseCamera.h and .cpp** - Camera base class implementation:
- Implements live view start/stop logic with thread management
- Manages static TCPIPServerDataRelay for image data streaming (port ID_IMAGE_DATA_RELAY_CONNECTION_PORT)
- Handles image scale min/max settings for brightness/contrast adjustment
- Provides camera synchronization controls for multi-camera systems
- Implements workflow thread lifecycle (m_workflowThread, m_workflowThreadRun)
- Processes camera-specific commands (exposure, ROI, trigger mode, etc.)
- Sends image options to display clients via sendImageOptionsToDisplayClients()

Key member variables:
```cpp
static TCPIPServerDataRelay m_liveViewDataRelay;  // Shared relay server for all cameras
static std::atomic<uint32_t> m_imageScaleMin;     // Brightness lower bound
static std::atomic<uint32_t> m_imageScaleMax;     // Brightness upper bound
static std::atomic_int m_numberOfCameras;         // Camera count for synchronization
std::atomic_bool m_workflowThreadRun;             // Live view thread control flag
std::thread m_workflowThread;                     // Worker thread for image acquisition
```

**Subsystems/Camera/PCOBase.h and .cpp** - PCO camera implementation:
- Implements liveViewAcquireImage() for continuous or single-shot image capture
- Manages PCO SDK buffer allocation and image retrieval
- Handles camera arming, recording state transitions, and error recovery
- Processes 16-bit image data from camera buffers
- Constructs ImageData structures with header and pixel data
- Queues images to the live view data relay via m_liveViewDataRelay.queueImageData()
- Implements takeWorkflowStack() for automated acquisition sequences
- Provides various acquisition classes for different workflow types (ZStack, Tile, OPT, etc.)

Constants defined:
```cpp
#define LIVE_VIEW_BUFFER_ARRAY_SIZE     4
#define LIVE_VIEW_IMAGE_TIMEOUT_MS      2000
```

**Subsystems/Camera/PCOCamera.h and PCOCamera.cpp** - Specific PCO camera wrapper that inherits from PCOBase and provides camera-specific initialization.

#### Workflow Management

**Workflow/WorkflowControl.h and .cpp** - Workflow orchestration:
- Manages execution of complex acquisition sequences (z-stacks, tiles, time-lapse, OPT)
- Coordinates camera, stage, laser, and illumination subsystems during workflows
- Provides callback mechanism for workflow status updates
- Maintains workflow settings and state through WorkflowData
- Implements thread-based workflow execution to prevent blocking
- Supports workflow types: ZStack, ZStackMovie, ZStackAPI, Tile, ZSweep, OPT, OPTZStack

**Workflow/WorkflowBase.h** - Base class for workflow implementations:
- Defines common workflow operations (position setting, illumination control, stage movement)
- Provides stackTake() method that delegates to camera subsystem
- Handles stage velocity adjustments for continuous scanning
- Manages illumination path configuration and filtering

**Workflow/WorkflowDataStack.h** - Data structure containing:
- Reference to WFSettings (workflow configuration)
- Vector of StageControl pointers for motion control
- Reference to ScopeBoard for synchronized triggering

#### Camera Acquisition Classes

**Subsystems/Camera/AcquisitionBase.h** - Base class for acquisition workflows:
- Coordinates image capture with stage movement and triggering
- Manages multiple threaded operations: imaging, max projection, stack streaming, storage
- Implements image queuing and processing pipelines
- Handles TIFF file creation with metadata
- Provides acquisition error handling and disk fault detection
- Logs timing information and stage positions during acquisition

**Subsystems/Camera/AcquisitionSupport.h/cpp** - Support structures and utilities for acquisition operations, likely containing structures like CaptureModeSettings and CameraImageSettings.

Various specialized acquisition classes (AcquisitionZStack.h, AcquisitionTile.h, AcquisitionOPT.h, etc.) implement specific acquisition patterns by extending AcquisitionBase.

### Key Functions and Data Structures Related to Live View

#### Live View Command Processing Flow

The live view system operates through a command-driven architecture:

1. **Command Reception** (ControlServer::processClientData):
```cpp
int ControlServer::processClientData(int clientfd)
{
    SCommand scmd;
    int bytesRead = readData(clientfd, (void*) &scmd, sizeof(scmd), ioTimeoutMS);
    if (bytesRead == sizeof(scmd) && scmd.isValid()) {
        // Read additional data if present
        // Call processCommands(clientfd, cmdBuffer)
    }
}
```

2. **Live View Start Processing**:
```cpp
case COMMAND_CODES_CAMERA_LIVE_VIEW_START:
    if (1 == scmd.int32Data0) {
        // Enable "update on change" mode
        setSystemState(COMMAND_CODES_SYSTEM_STATE_LIVE_VIEW);
        m_liveViewUpdateOnChange = true;
        SystemLog::logger()->info(SPD_TRACE("Start live view - update on change"));
    } else {
        // Normal continuous live view mode
        m_systemControl.exeCommand(scmd, additionalData);
    }
    updateClientsSystemSettings();
    break;
```

3. **Camera Thread Spawning** (BaseCamera::liveviewStart):
```cpp
void BaseCamera::liveviewStart(SCommand& scmd, bool runContinuous)
{
    std::lock_guard<std::mutex> guard(m_workflowThreadMutex);

    if (false == m_workflowThreadRun) {
        if (m_workflowThread.joinable())
            m_workflowThread.join();

        m_workflowThreadRun = true;
        m_workflowThread = std::thread(&BaseCamera::getLiveViewImages,
                                       this, runContinuous, scmd.subsystemID);
        statusStr = "Live view thread started";
    }

    scmd.status = 1;
    sendCameraDataToController(scmd, statusStr);
}
```

4. **Image Acquisition Loop** (BaseCamera::getLiveViewImages):
```cpp
void BaseCamera::getLiveViewImages(bool runContinuous, unsigned long clientID)
{
    SCommand scmd;
    scmd.status = 1;
    scmd.subsystemID = m_subsystemID;
    scmd.cmd = (runContinuous ? COMMAND_CODES_SYSTEM_STATE_LIVE_VIEW
                               : COMMAND_CODES_SYSTEM_STATE_SNAP_SHOT);

    triggerCallbacks(scmd);

    // Calls derived class implementation (e.g., PCOBase::liveViewAcquireImage)
    if (liveViewAcquireImage(runContinuous, clientID) == false)
        SystemLog::logger()->error(SPD_TRACE("live view image acquire - FAILED"));

    // Restore IDLE state after completion
    setSystemState(COMMAND_CODES_SYSTEM_STATE_IDLE);
    scmd.cmd = COMMAND_CODES_SYSTEM_STATE_IDLE;
    triggerCallbacks(scmd);
}
```

5. **PCO Camera Image Capture** (PCOBase::liveViewAcquireImage):
```cpp
bool PCOBase::liveViewAcquireImage(bool runContinuous, unsigned long clientID)
{
    // Prepare ImageData structure
    ImageData imageData;
    imageData.imageHeader.imageSize = imageSizeInBytes;
    imageData.imageHeader.imageHeight = imageHeight;
    imageData.imageHeader.imageWidth = imageWidth;
    imageData.imageHeader.imageScaleMin = m_imageScaleMin;
    imageData.imageHeader.imageScaleMax = m_imageScaleMax;
    imageData.imageHeader.optionsSettings1 = IMAGE_OPTION_IMAGE_DATA;

    if (AllocateImageBuffers(imageWidth, imageHeight, imageSizeInBytes)) {
        if (SetRecordingState(1)) {  // Start camera recording
            if (AddBufferEx(imageWidth, imageHeight)) {
                imageData.dataBuffer.resize(imageSizeInBytes);

                do {
                    if (WaitforNextBufferNum(current_buffer_num)) {
                        // Get image from camera buffer
                        PCO_GetBuffer(m_cameraHandle, current_buffer_num,
                                      &current_buffer_addr, &not_used);
                        memcpy(&imageData.dataBuffer[0], current_buffer_addr,
                               imageSizeInBytes);

                        // Update dynamic settings from UI
                        imageData.imageHeader.imageScaleMin = m_imageScaleMin;
                        imageData.imageHeader.imageScaleMax = m_imageScaleMax;
                        imageData.imageHeader.deviceIndex = m_deviceIndex;
                        imageData.imageHeader.optionsSettings1 = m_cameraOptionsSettings1;
                        imageData.imageHeader.optionsSettings2 = m_cameraOptionsSettings2;

                        // Queue image for relay to clients
                        m_liveViewDataRelay.queueImageData(imageData);
                    }

                    // Re-add buffer for next frame
                    PCO_AddBufferEx(m_cameraHandle, 0, 0, current_buffer_num,
                                    imageWidth, imageHeight, 16);

                } while (runContinuous && m_workflowThreadRun);

                PCO_CancelImages(m_cameraHandle);
            }
            SetRecordingState(0);  // Stop recording
        }
        FreeImageBuffers();
    }

    m_workflowThreadRun = false;
    return true;
}
```

6. **Image Options Broadcasting** (BaseCamera::sendImageOptionsToDisplayClients):
```cpp
void BaseCamera::sendImageOptionsToDisplayClients()
{
    if (m_controlCamera) {
        ImageData imageData;
        imageData.imageHeader.imageSize = 0;  // Zero size indicates options-only
        imageData.imageHeader.imageHeight = 0;
        imageData.imageHeader.imageWidth = 0;
        imageData.imageHeader.imageScaleMin = m_imageScaleMin;
        imageData.imageHeader.imageScaleMax = m_imageScaleMax;
        imageData.imageHeader.deviceIndex = m_deviceIndex;
        imageData.imageHeader.optionsSettings1 = m_cameraOptionsSettings1;
        imageData.imageHeader.optionsSettings2 = m_cameraOptionsSettings2;
        imageData.dataBuffer.clear();
        m_liveViewDataRelay.queueImageData(imageData);
    }
}
```

#### Live View "Update on Change" Feature

The ControlServer implements a sophisticated "update on change" mode that automatically takes snapshots when system parameters change:

**Purpose**: Provide responsive UI feedback during parameter adjustments without continuous streaming overhead.

**Implementation** (ControlServer::liveViewUpdateOnChange and liveViewUpdateOnChangeThread):

```cpp
void ControlServer::liveViewUpdateOnChange(const SCommand& scmd)
{
    // Check if command triggers a new snapshot
    switch (scmd.cmd) {
        case COMMAND_CODES_LASER_LEVEL_SET:
        case COMMAND_CODES_LASER_ENABLE:
        case COMMAND_CODES_CAMERA_EXPOSURE_SET:
        case COMMAND_CODES_CAMERA_ROI_LEFT_SET:
        case COMMAND_CODES_STAGE_POSITION_SET:
        case COMMAND_CODES_FILTER_WHEEL_SET_POSITION:
            // Command triggers image update
            break;
        default:
            return;  // Don't trigger update
    }

    // Track motion state for stages and filter wheel
    m_liveViewUpdateOnChangeInMotion = false;
    if (scmd.cmdStage() && m_systemControl.installedStages()) {
        m_liveViewUpdateOnChangeInMotion = true;  // Wait for motion to stop
    }
    m_liveViewUpdateOnChangeFilterWheelInMotion = m_systemControl.installedFilterWheel();

    // Spawn update thread if not already running
    if (!m_liveViewUpdateOnChangeThreadRunning) {
        if (m_liveViewUpdateOnChangeThread.joinable())
            m_liveViewUpdateOnChangeThread.join();

        m_liveViewUpdateOnChangeThreadRunning = true;
        m_liveViewUpdateOnChangeThread = std::thread(
            &ControlServer::liveViewUpdateOnChangeThread, this);
    }
}

void ControlServer::liveViewUpdateOnChangeThread()
{
    SystemLog::logger()->info(SPD_TRACE("liveViewUpdateOnChangeThread - started"));
    SCommand scmd;

    // Wait for filter wheel to reach target position (timeout 5 seconds)
    if (m_liveViewUpdateOnChangeFilterWheelInMotion) {
        TimedOutMS filterWheelMotionStopTimeOut(5000);
        while (m_liveViewUpdateOnChangeFilterWheelInMotion &&
               m_liveViewUpdateOnChangeThreadRunning) {
            scmd.cmd = COMMAND_CODES_FILTER_WHEEL_MOTION_STOPPED;
            m_systemControl.exeCommand(scmd);
            std::this_thread::sleep_for(std::chrono::milliseconds(1));

            if (filterWheelMotionStopTimeOut.timedOut()) break;
        }
    }

    // Start live view if not already running
    if (m_liveViewUpdateOnChangeThreadRunning && !m_systemControl.liveviewRunning()) {
        scmd.clear();
        scmd.cmd = COMMAND_CODES_CAMERA_LIVE_VIEW_START;
        scmd.status = 1;
        m_systemControl.exeCommand(scmd);
    }

    // Wait for stage motion to complete
    while (true) {
        if (!m_liveViewUpdateOnChangeThreadRunning) break;
        if (!m_liveViewUpdateOnChangeInMotion) break;
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }

    // Stop live view to capture final image
    if (m_liveViewUpdateOnChangeThreadRunning) {
        scmd.clear();
        scmd.cmd = COMMAND_CODES_CAMERA_LIVE_VIEW_STOP;
        scmd.status = 1;
        m_systemControl.exeCommand(scmd);
    }

    m_liveViewUpdateOnChangeThreadRunning = false;
}
```

**Operation**:
1. Client sends a LIVE_VIEW_START command with int32Data0=1 to enable update-on-change mode
2. System enters LIVE_VIEW state but doesn't start continuous streaming
3. When a relevant command is received (e.g., move stage, change laser power), the system:
   - Waits for filter wheel motion to stop
   - Starts a single-shot live view capture
   - Waits for stage motion to complete
   - Stops live view, sending one final frame with settled parameters
4. Client receives updated image reflecting the new system state
5. Process repeats for each parameter change

**Benefits**:
- Reduced network bandwidth compared to continuous streaming
- Real-time preview during parameter adjustments
- Ensures displayed image matches current system configuration

#### Stop Live View Processing

```cpp
case COMMAND_CODES_CAMERA_LIVE_VIEW_STOP:
    SystemLog::logger()->info(SPD_TRACE("Stop live view"));
    m_liveViewUpdateOnChange = false;
    m_liveViewUpdateOnChangeThreadRunning = false;
    processCmdStatus = (int) m_systemControl.exeCommand(scmd, additionalData);
    setSystemState(COMMAND_CODES_SYSTEM_STATE_IDLE);
    break;

void BaseCamera::liveviewStop(SCommand& scmd)
{
    m_workflowThreadRun = false;  // Signal thread to exit
    if (m_workflowThread.joinable()) {
        m_workflowThread.join();
        statusStr = "Live view thread terminated";
        scmd.status = 1;
    } else {
        statusStr = "Live view thread not running";
    }
    sendCameraDataToController(scmd, statusStr);
}
```

### Command Codes and Protocol Specifications

The system uses an enum-based command code system (defined in SystemCommands.h, not shown but referenced throughout). Key live view related command codes identified:

**System State Commands**:
- `COMMAND_CODES_SYSTEM_STATE_IDLE` - System ready for new commands
- `COMMAND_CODES_SYSTEM_STATE_LIVE_VIEW` - Live view streaming active
- `COMMAND_CODES_SYSTEM_STATE_SNAP_SHOT` - Single frame capture
- `COMMAND_CODES_SYSTEM_STATE_WORK_FLOW_RUNNING` - Automated acquisition in progress
- `COMMAND_CODES_SYSTEM_STATE_DISCONNECTED` - No client connection

**Camera Commands**:
- `COMMAND_CODES_CAMERA_LIVE_VIEW_START` - Begin continuous live view
- `COMMAND_CODES_CAMERA_LIVE_VIEW_STOP` - End live view streaming
- `COMMAND_CODES_CAMERA_SNAPSHOT_GET` - Capture single frame
- `COMMAND_CODES_CAMERA_EXPOSURE_SET/GET` - Exposure time control
- `COMMAND_CODES_CAMERA_FPS_SET/GET` - Frame rate control
- `COMMAND_CODES_CAMERA_TRIGGER_MODE_SET/GET` - Hardware/software triggering
- `COMMAND_CODES_CAMERA_ROI_LEFT/RIGHT/TOP/BOTTOM_SET/GET` - Region of interest
- `COMMAND_CODES_CAMERA_SET_IMAGE_MIN_VALUE` - Brightness lower bound
- `COMMAND_CODES_CAMERA_SET_IMAGE_MAX_VALUE` - Brightness upper bound
- `COMMAND_CODES_CAMERA_SET_CAMERA1AND2_OPTIONS` - View options (zoom, overlay)
- `COMMAND_CODES_CAMERA_WORK_FLOW_START/STOP` - Workflow control
- `COMMAND_CODES_CAMERA_RESET` - Reset camera to initial state

**UI Control Commands**:
- `COMMAND_CODES_UI_LIVE_VIEW_UPDATE_ON_CHANGE_ENABLED` - Enable responsive mode
- `COMMAND_CODES_UI_LIVE_VIEW_UPDATE_ON_CHANGE_DISABLED` - Standard streaming mode

**Other Relevant Commands**:
- `COMMAND_CODES_STAGE_POSITION_SET` - Move stage to position
- `COMMAND_CODES_STAGE_MOTION_STOPPED` - Stage reached target
- `COMMAND_CODES_LASER_LEVEL_SET` - Set laser power
- `COMMAND_CODES_FILTER_WHEEL_SET_POSITION` - Change filter
- `COMMAND_CODES_FILTER_WHEEL_MOTION_STOPPED` - Filter wheel settled
- `COMMAND_CODES_COMMON_MESSAGE_FROM_CONTROLLER` - Status/error message

### Data Structure: SCommand

The SCommand structure is the fundamental communication protocol unit. While the full definition isn't shown, the code reveals its structure:

```cpp
struct SCommand {
    COMMAND_CODES cmd;           // Command identifier
    uint8_t status;              // Success/failure flag
    SUB_SYSTEM_IDS subsystemID;  // Target subsystem
    int32_t hardwareID;          // Device instance index
    unsigned long clientID;      // Client identifier
    int32_t int32Data0;          // General purpose integer data
    int32_t int32Data1;          // Additional integer data
    double doubleData;           // Floating point data
    uint32_t additionalDataBytes;// Size of following data
    char buffer[...];            // String or binary data buffer

    bool isValid();              // Validation check
    void strToBuffer(const std::string& str);  // String serialization
    bool cmdStage();             // Check if stage command
    void clear();                // Reset structure
};
```

The structure is 64-bit aligned (verified in main.cpp) and can be extended with additional data transmitted separately after the base structure.

### How Components Interact with the Live View Subsystem

#### Initialization and Discovery

1. **Application Startup** (main.cpp):
   - Creates SystemLog for logging
   - Instantiates SystemControl
   - Calls systemControl.systemStart() to discover hardware
   - Creates ControlServer with reference to SystemControl
   - Starts TCP server listening thread

2. **Hardware Discovery** (SystemControl):
   - Scans serial ports and USB devices
   - Detects PCO cameras via PCO SDK
   - Creates PCOCamera or PCOBase instances for each detected camera
   - Adds camera pointers to m_vpControlCameras vector
   - Cameras automatically start TCPIPServerDataRelay for image streaming

3. **Callback Registration** (ControlServer constructor):
   ```cpp
   m_systemControl.RegisterDataAvailableCameras(this,
       &ControlServer::callbackCameras, NULL);
   ```
   Allows ControlServer to receive notifications when cameras send data.

#### Runtime Operation

**Client Connection**:
1. Client connects to ControlServer on main control port
2. ControlServer accepts connection, assigns file descriptor
3. Client sends COMMAND_CODES_COMMON_UI_CONTROLLER_ID_GET to authenticate
4. ControlServer responds with microscope configuration
5. Client connects to image data relay port for live view streaming

**Live View Activation**:
1. Client sends LIVE_VIEW_START command to ControlServer
2. ControlServer updates system state and forwards to SystemControl
3. SystemControl routes command to camera subsystem(s)
4. BaseCamera spawns worker thread, starts acquisition loop
5. PCOBase captures frames from camera hardware
6. ImageData structures queued to TCPIPServerDataRelay
7. Relay broadcasts images to all connected viewers
8. Client receives images and displays via LiveView application

**Parameter Adjustment**:
1. Client sends command (e.g., CAMERA_EXPOSURE_SET)
2. ControlServer processes and forwards to camera
3. BaseCamera updates camera hardware settings
4. If "update on change" active, ControlServer triggers new snapshot
5. Updated image reflects new parameter value

**State Synchronization**:
- ControlServer maintains callback mutexes for thread-safe notifications
- System state changes broadcast to all connected clients
- Cameras use atomic variables for thread-safe parameter updates
- Callbacks trigger immediately when subsystems have new data

#### Workflow Integration

When executing complex acquisition workflows:

1. **Workflow Start**:
   - Client sends WORK_FLOW_START with workflow JSON/XML settings
   - ControlServer delegates to WorkflowControl
   - WorkflowControl spawns thread, parses settings
   - Specific workflow class instantiated (e.g., WorkflowZStack)

2. **Stack Acquisition**:
   - WorkflowBase calls m_systemControl.stackTake(workflowDataStack)
   - SystemControl routes to camera subsystem
   - CameraControl::stackTake() invoked with workflow parameters
   - PCOBase creates appropriate AcquisitionBase subclass
   - AcquisitionBase coordinates:
     - Stage movement via WorkflowDataStack stage controls
     - Camera triggering via ScopeBoard or software trigger
     - Image capture and storage to disk
     - Max projection calculation
     - Stack streaming to viewing clients
     - Metadata logging

3. **Parallel Operation**:
   - Multiple threads handle different aspects:
     - threadImaging: Camera image acquisition
     - threadMaxProjection: Real-time max projection calculation
     - threadStackStreaming: Send images to live viewers
     - threadStackStorage: Write images to disk with TIFF format
   - Queues coordinate between threads for efficient pipeline

4. **Workflow Completion**:
   - Camera returns to IDLE state
   - Workflow thread terminates
   - ControlServer updates system state
   - Clients notified via callbacks

### Network Architecture

The system uses a dual-port architecture:

**Control Port** (ID_CONTROL_SERVER_CONNECTION_PORT):
- Command and status communication
- SCommand structure exchange
- Multiple clients supported (ID_CONTROL_SERVER_CONNECTIONS_MAX)
- Bidirectional communication with timeouts

**Image Data Port** (ID_IMAGE_DATA_RELAY_CONNECTION_PORT):
- Unidirectional image streaming from server to clients
- TCPIPServerDataRelay handles multiple viewer connections (ID_IMAGE_DATA_RELAY_CONNECTIONS_MAX = 64)
- ImageData structures with header and pixel buffer
- Shared by all camera instances (static member)
- Non-blocking queue-based architecture for high throughput

### Key Design Patterns

1. **Callback-Based Notification System**: Subsystems register callbacks with ControlServer to push data updates without polling.

2. **Template-Based Registration**: Generic template methods allow type-safe callback registration for any class:
   ```cpp
   template<typename T_object>
   void RegisterDataAvailableCameras(T_object* object,
       void(T_object::*function)(void*, void*), void* userData);
   ```

3. **State Machine Architecture**: System transitions through well-defined states, with commands validated against current state.

4. **Thread-Per-Task**: Separate threads for live view, workflows, update-on-change, preventing blocking operations.

5. **Static Shared Resources**: TCPIPServerDataRelay and image scaling parameters shared across all camera instances to conserve resources.

6. **Mutex-Protected State**: Atomic variables and mutexes ensure thread-safe access to shared state.

7. **Virtual Function Hierarchy**: BaseCamera defines interface, PCOBase implements PCO-specific logic, allowing support for other camera vendors.

### Summary

The ControlSystem is a sophisticated microscope control server that orchestrates hardware subsystems and provides live image streaming to remote clients. The live view functionality is implemented through a multi-layered architecture: ControlServer manages client connections and command routing, SystemControl coordinates hardware subsystems, BaseCamera provides camera abstraction with thread-based acquisition, PCOBase implements PCO-specific capture logic, and TCPIPServerDataRelay broadcasts images to multiple viewers. The system supports both continuous streaming and responsive "update on change" modes, integrates seamlessly with complex workflow automation, and maintains robust state management through callback-based communication. The modular design with clear separation of concerns enables support for multiple camera types, concurrent operation of multiple cameras, and flexible viewing configurations while maintaining high performance and responsiveness.

## Command Sequence Comparison: Working vs. Current Implementation

This section compares the command sequences from a working laser snapshot live mode session (captured from logs) with the command capabilities in the current Python implementation.

### Working Command Sequence (from SwitchToLaserSnapshotLiveMode.txt)

The following command sequence successfully enables laser preview mode and starts live viewing:

#### 1. LED Disable (Command Index 110)
```
cmd = 0x00004003 (16387, LEDCommands.DISABLE)
status = 0
hardwareID = 0
subsystemID = None
clientID = 26
int32Data0 = 0
int32Data1 = 0
int32Data2 = 0
cmdDataBits0 = 0x80000000
doubleData = 0
additionalDataBytes = 0
```
**Purpose**: Disable any active LED before enabling laser

#### 2. Laser Preview Enable (Command Index 111)
```
cmd = 0x00002004 (8196, LaserCommands.PREVIEW_ENABLE)
status = 0
hardwareID = 0
subsystemID = None
clientID = 26
int32Data0 = 2    ← Laser index (user laser line 2)
int32Data1 = 0
int32Data2 = 0
cmdDataBits0 = 0x80000000
doubleData = 0
additionalDataBytes = 0
```
**Purpose**: Enable laser index 2 in preview mode (external trigger)
**Server Actions**:
- Disables all laser lines first
- Enables external trigger on specified laser line
- Moves filter wheel to position 2300 (position 6)

#### 3. Illumination Left Enable (Command Index 112)
```
cmd = 0x00007004 (28676, IlluminationCommands.LEFT_ENABLE)
status = 0
hardwareID = 0
subsystemID = None
clientID = 26
int32Data0 = 0
int32Data1 = 0
int32Data2 = 0
cmdDataBits0 = 0x80000000
doubleData = 0
additionalDataBytes = 0
```
**Purpose**: Configure illumination waveform for synchronized imaging
**Server Actions**:
- Sets illumination waveform: `ILWAVE 200 1500 290 -1 -1`
- Coordinates camera exposure timing with illumination

#### 4. Take Single Image / Snapshot (Command Index 113)
```
cmd = 0x00003006 (12294, CameraCommands.SNAPSHOT)
status = 0
hardwareID = 0
subsystemID = None
clientID = 26
int32Data0 = 0
int32Data1 = 0
int32Data2 = 0
cmdDataBits0 = 0x80000000
doubleData = 0
additionalDataBytes = 0
```
**Purpose**: Capture a single test image before starting continuous live view
**Server Actions**:
- System state changes to SNAP_SHOT
- Camera arms and starts recording
- Captures one frame and returns image data
- System returns to IDLE state

#### 5. Start Continuous Imaging / Live View (Command Index 114)
```
cmd = 0x00003007 (12295, CameraCommands.LIVE_VIEW_START)
status = 0
hardwareID = 0
subsystemID = None
clientID = 26
int32Data0 = 0    ← 0 = normal continuous mode (not update-on-change)
int32Data1 = 0
int32Data2 = 0
cmdDataBits0 = 0x80000000
doubleData = 0
additionalDataBytes = 0
```
**Purpose**: Start continuous live view streaming
**Server Actions**:
- System state changes to LIVE_VIEW
- Spawns worker thread for continuous acquisition
- Camera starts recording and streaming frames
- Images broadcast to all connected viewers via data relay port

#### 6. Stop Continuous Imaging (Command Index 115 - after ~3 seconds)
```
cmd = 0x00003008 (12296, CameraCommands.LIVE_VIEW_STOP)
status = 0
hardwareID = 0
subsystemID = None
clientID = 26
int32Data0 = 0
int32Data1 = 0
int32Data2 = 0
cmdDataBits0 = 0x80000000
doubleData = 0
additionalDataBytes = 0
```
**Purpose**: Stop live view streaming
**Server Actions**:
- Signals worker thread to terminate
- Cancels camera images and stops recording
- System state returns to IDLE

### Current Python Implementation Capabilities

The current Python implementation (`py2flamingo`) has the following command codes defined:

#### LED Commands (py2flamingo/core/command_codes.py)
```python
class LEDCommands:
    SET_VALUE = 0x4001       # 16385 - Set LED brightness
    ENABLE = 0x4002          # 16386 - Enable LED
    DISABLE = 0x4003         # 16387 - Disable LED ✓
    SELECTION_CHANGE = 0x4006 # 16390 - Change LED selection
```

#### Laser Commands
```python
class LaserCommands:
    LEVEL_SET = 0x2001       # 8193 - Set laser power level
    LEVEL_GET = 0x2001       # 8193 - Get laser power level
    PREVIEW_ENABLE = 0x2004  # 8196 - Enable laser preview mode ✓
    PREVIEW_DISABLE = 0x2005 # 8197 - Disable laser preview mode
    ALL_DISABLE = 0x2007     # 8199 - Disable all laser lines
```

#### Illumination Commands
```python
class IlluminationCommands:
    LEFT_ENABLE = 0x7004     # 28676 - Enable left illumination ✓
    LEFT_DISABLE = 0x7005    # 28677 - Disable left illumination
```

#### Camera Commands
```python
class CameraCommands:
    SNAPSHOT = 0x3006        # 12294 - Take a single image ✓
    LIVE_VIEW_START = 0x3007 # 12295 - Start continuous imaging ✓
    LIVE_VIEW_STOP = 0x3008  # 12296 - Stop continuous imaging ✓
    EXPOSURE_SET = 0x3001    # 12289 - Set exposure time
    EXPOSURE_GET = 0x3002    # 12290 - Get exposure time
    WORKFLOW_START = 0x3004  # 12292 - Start workflow
    WORKFLOW_STOP = 0x3005   # 12293 - Stop workflow
```

**✓ = All required command codes are present in the Python implementation**

### Key Observations and Recommendations

#### 1. Command Code Coverage
**Status**: ✅ Complete
- All six commands from the working sequence are defined in the Python implementation
- Command values match exactly between C++ server and Python client

#### 2. Command Parameter Requirements
**Critical Parameters** (from working sequence):
- `int32Data0`: Used for laser index selection (value = 2 in the log)
- `cmdDataBits0`: Always set to `0x80000000` in working examples
- `status`, `hardwareID`, `subsystemID`: Typically set to 0 for client requests

**Current Implementation**: The `ProtocolEncoder` class supports all these parameters through its `encode_command()` method.

#### 3. Recommended Command Sequence for Python Implementation

To replicate the working sequence, the Python camera controller should send commands in this order:

```python
from py2flamingo.core.command_codes import (
    LEDCommands, LaserCommands, IlluminationCommands, CameraCommands
)
from py2flamingo.core.protocol_encoder import ProtocolEncoder

encoder = ProtocolEncoder()

# Step 1: Disable LED
cmd_led_disable = encoder.encode_command(
    code=LEDCommands.DISABLE,
    params=[0, 0, 0, 0, 0, 0, 0x80000000]
)

# Step 2: Enable laser preview (laser index in int32Data0)
cmd_laser_preview = encoder.encode_command(
    code=LaserCommands.PREVIEW_ENABLE,
    params=[laser_index, 0, 0, 0, 0, 0, 0x80000000]  # laser_index = 1-4
)

# Step 3: Enable illumination
cmd_illumination = encoder.encode_command(
    code=IlluminationCommands.LEFT_ENABLE,
    params=[0, 0, 0, 0, 0, 0, 0x80000000]
)

# Step 4: Take test snapshot (optional but recommended)
cmd_snapshot = encoder.encode_command(
    code=CameraCommands.SNAPSHOT,
    params=[0, 0, 0, 0, 0, 0, 0x80000000]
)

# Step 5: Start live view
cmd_live_start = encoder.encode_command(
    code=CameraCommands.LIVE_VIEW_START,
    params=[0, 0, 0, 0, 0, 0, 0x80000000]  # int32Data0=0 for continuous mode
)

# Later: Stop live view
cmd_live_stop = encoder.encode_command(
    code=CameraCommands.LIVE_VIEW_STOP,
    params=[0, 0, 0, 0, 0, 0, 0x80000000]
)
```

#### 4. Timing Considerations

From the log timestamps:
- **LED disable → Laser enable**: 34ms delay
- **Laser enable → Illumination enable**: ~10s delay (likely user interaction)
- **Illumination → Snapshot**: 68ms delay
- **Snapshot → Live view start**: ~2.5s delay (image transfer time)
- **Live view duration**: ~3 seconds before stop

**Recommendations**:
- No artificial delays needed between LED disable and laser enable
- Allow ~100ms after illumination command before snapshot
- Wait for snapshot completion before starting live view
- Use the image data relay socket to receive live frames (port 53718 default)

#### 5. cmdDataBits0 Flag Analysis

The value `0x80000000` appears in all working commands. From the ControlSystem analysis:

**Known Flags**:
```cpp
COMMAND_DATA_BITS_TRIGGER_CALL_BACK           = 0x80000000  // Request response
COMMAND_DATA_BITS_EXPERIMENT_TIME_REMAINING   = 0x00000001
COMMAND_DATA_BITS_STAGE_POSITIONS_IN_BUFFER   = 0x00000002
COMMAND_DATA_BITS_MAX_PROJECTION              = 0x00000008
COMMAND_DATA_BITS_SAVE_TO_DISK                = 0x00000010
COMMAND_DATA_BITS_STAGE_NOT_UPDATE_CLIENT     = 0x00000020
COMMAND_DATA_BITS_STAGE_ZSWEEP                = 0x00000080
```

**Interpretation**: `0x80000000` is the `TRIGGER_CALL_BACK` flag, which:
- Requests the server to send a response confirming command execution
- Essential for GET commands (without it, no response is sent)
- Used in working SET commands for acknowledgment
- Should be included in all commands in the Python implementation

#### 6. Missing Components to Verify

**Image Data Reception**:
- Verify the data socket connection (port 53718 or configured port)
- Ensure `LiveViewControl` equivalent functionality exists in Python
- Confirm `ImageHeader` and `ImageData` structure parsing matches C++ definitions

**Laser/LED Controller Integration**:
- Verify `LaserLEDController` in Python correctly calls these command sequences
- Check that `enable_laser_for_preview()` sends the correct laser index
- Ensure LED disable is called before enabling laser

**Camera Service Integration**:
- Confirm `CameraService.start_live_view()` sends LIVE_VIEW_START command
- Verify the data reception thread properly handles `ImageData` structures
- Check that image callbacks are triggered correctly

### Summary and Next Steps

**Status**: The Python implementation has all the required command codes to replicate the working laser snapshot live mode sequence.

**Action Items**:
1. ✅ Verify all command codes are present (COMPLETE)
2. ⚠️ Check that `LaserLEDController.enable_laser_for_preview()` sends commands in correct order:
   - LED disable
   - Laser preview enable (with correct laser index)
   - Illumination left enable
3. ⚠️ Verify `CameraController.start_live_view()` sequence:
   - Optional: Send snapshot command first as test
   - Send LIVE_VIEW_START with int32Data0=0
   - Connect to data socket and start receiving images
4. ⚠️ Confirm `cmdDataBits0 = 0x80000000` is set for all commands
5. ⚠️ Test the complete sequence with hardware and compare logs with working example

**Expected Behavior** (when correctly implemented):
- LED turns off
- Selected laser line activates in preview mode (external trigger ready)
- Filter wheel moves to appropriate position for that laser
- Illumination waveform configured
- Camera begins streaming 16-bit images via data relay socket
- Images displayed in live viewer with correct intensity scaling
- Laser illumination synchronized with camera exposure via hardware triggering
