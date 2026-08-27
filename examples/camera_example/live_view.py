import glob
import struct
import cv2
import numpy as np
import serial

ports = glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*')
if not ports:
    print("Error: No ESP32 board detected!")
    exit(1)

PORT = ports[0]
BAUD = 921600

ser = serial.Serial(PORT, BAUD, timeout=1)
ser.reset_input_buffer()

print(f"Connected to {PORT} at {BAUD} baud. Live View active...")

HEADER = b'\xAA\xBB\xCC\xDD'
buffer = bytearray()

while True:
    try:
        data = ser.read(ser.in_waiting or 2048)
        if data:
            buffer.extend(data)

        # Search for frame header
        header_pos = buffer.find(HEADER)
        if header_pos != -1:
            # Check if header + 4-byte size is available
            if len(buffer) >= header_pos + 8:
                img_size = struct.unpack('<I', buffer[header_pos + 4 : header_pos + 8])[0]

                # Sanity check frame size
                if 200 < img_size < 200000:
                    frame_start = header_pos + 8
                    frame_end = frame_start + img_size

                    if len(buffer) >= frame_end:
                        frame_data = buffer[frame_start:frame_end]
                        buffer = buffer[frame_end:]  # Slice out processed frame

                        # Strict JPEG check: Starts with 0xFFD8 and ends with 0xFFD9
                        if frame_data.startswith(b'\xff\xd8') and frame_data.endswith(b'\xff\xd9'):
                            np_arr = np.frombuffer(frame_data, dtype=np.uint8)
                            frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

                            if frame is not None:
                                cv2.imshow("ESP32-S3 OV3660 Clean Stream", frame)
                    else:
                        # Full frame hasn't arrived yet
                        pass
                else:
                    # Corrupted size, slide buffer forward
                    buffer = buffer[header_pos + 4:]
        else:
            # Discard stale buffer data
            if len(buffer) > 16384:
                buffer = buffer[-4096:]

        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    except KeyboardInterrupt:
        break
    except Exception as e:
        print(f"Error: {e}")

ser.close()
cv2.destroyAllWindows()