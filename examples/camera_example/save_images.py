import os
import time
import glob
import re
import serial

SAVE_DIR = "/home/mihai/esp32-camera/examples/camera_example/main/images"
BAUD = 115200

ports = glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*')
if not ports:
    print("Error: No ESP32 board found!")
    exit(1)

PORT = ports[0]
os.makedirs(SAVE_DIR, exist_ok=True)
ser = serial.Serial(PORT, BAUD, timeout=15)

print(f"Connected to {PORT}. Saving images to {SAVE_DIR}...")

img_count = 0
receiving = False
hex_buffer = []

while True:
    try:
        line = ser.readline().decode('ascii', errors='ignore').strip()
        
        if line == "IMAGE_START":
            receiving = True
            hex_buffer = []
            print("Receiving image data...")
            continue
            
        elif line == "IMAGE_END" and receiving:
            receiving = False
            raw_hex = "".join(hex_buffer)
            
            # Clean out any accidental logs or whitespace
            clean_hex = re.sub(r'[^0-9a-fA-F]', '', raw_hex)
            
            # Ensure even length for byte conversion
            if len(clean_hex) % 2 != 0:
                clean_hex = clean_hex[:-1]
                
            if len(clean_hex) > 100:
                img_count += 1
                filename = os.path.join(SAVE_DIR, f"image_{int(time.time())}_{img_count}.jpg")
                with open(filename, "wb") as f:
                    f.write(bytes.fromhex(clean_hex))
                print(f"✓ Saved: {filename} ({len(clean_hex)//2} bytes)")
            continue

        if receiving:
            hex_buffer.append(line)
            
    except KeyboardInterrupt:
        break
    except Exception as e:
        print(f"Error: {e}")