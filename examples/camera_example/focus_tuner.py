import cv2
import urllib.request
import numpy as np

ESP32_IP = "10.42.0.153"  # Your ESP32 IP
STREAM_URL = f"http://{ESP32_IP}/"

def main():
    print(f"Opening focus tuner on {STREAM_URL}...")
    stream = urllib.request.urlopen(STREAM_URL, timeout=5)
    bytes_buf = bytearray()
    
    max_focus_score = 0.0
    cv2.namedWindow("ESP32 Focus Calibration", cv2.WINDOW_NORMAL)

    while True:
        bytes_buf.extend(stream.read(4096))
        a = bytes_buf.find(b'\xff\xd8')
        b = bytes_buf.find(b'\xff\xd9')

        if a != -1 and b != -1 and b > a:
            jpg = bytes(bytes_buf[a:b+2])
            bytes_buf = bytes_buf[b+2:]

            frame = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                continue

            h, w = frame.shape[:2]

            # 1. Define central focus ROI (where objects will be scooped/detected)
            rw, rh = int(w * 0.5), int(h * 0.5)
            rx, ry = int((w - rw) / 2), int((h - rh) / 2)
            roi = frame[ry:ry+rh, rx:rx+rw]

            # 2. Calculate Laplacian variance (higher = sharper edges)
            gray_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            focus_score = cv2.Laplacian(gray_roi, cv2.CV_64F).var()

            if focus_score > max_focus_score:
                max_focus_score = focus_score

            # 3. Visual feedback (Green if close to peak, Yellow/Red if out of focus)
            color = (0, 255, 0) if focus_score >= (max_focus_score * 0.85) else (0, 165, 255)
            cv2.rectangle(frame, (rx, ry), (rx + rw, ry + rh), color, 2)

            # 4. HUD
            cv2.putText(frame, f"Focus Score: {focus_score:.1f}", (15, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            cv2.putText(frame, f"Peak Score : {max_focus_score:.1f}", (15, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
            cv2.putText(frame, "Rotate lens until score hits maximum", (15, h - 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

            cv2.imshow("ESP32 Focus Calibration", frame)

            key = cv2.waitKey(1) & 0xFF
            if key in (27, ord('q')):
                break
            elif key == ord('r'):  # Reset peak score if you change the test distance
                max_focus_score = 0.0

    cv2.destroyAllWindows()

if __name__ == '__main__':
    main()