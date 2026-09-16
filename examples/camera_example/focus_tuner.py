import cv2
import urllib.request
import numpy as np
import time
import threading

STREAM_URL = "http://10.42.0.153/"

class StreamCapture:
    def __init__(self, url):
        self.url = url
        self.frame = None
        self.lock = threading.Lock()
        self.stopped = False
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        while not self.stopped:
            try:
                stream = urllib.request.urlopen(self.url, timeout=3.0)
                buf = bytearray()
                while not self.stopped:
                    chunk = stream.read(16384)
                    if not chunk:
                        break
                    buf.extend(chunk)

                    a = buf.find(b'\xff\xd8')
                    b = buf.find(b'\xff\xd9')
                    if a != -1 and b != -1 and b > a:
                        jpg = bytes(buf[a:b+2])
                        buf = buf[b+2:]
                        img = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
                        if img is not None and img.size > 0:
                            with self.lock:
                                self.frame = img
            except Exception:
                time.sleep(0.5)

    def get(self):
        with self.lock:
            return self.frame.copy() if self.frame is not None else None

def main():
    print(f"Connecting to optical tuning feed at {STREAM_URL}...")
    cap = StreamCapture(STREAM_URL)
    cv2.namedWindow("Manual Lens Alignment & Focus", cv2.WINDOW_NORMAL)

    peaking_enabled = True
    zoom_enabled = False
    grid_enabled = True

    while True:
        frame = cap.get()
        if frame is None:
            time.sleep(0.01)
            continue

        h, w = frame.shape[:2]

        # Calculate focus metric (Laplacian variance)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        focus_score = cv2.Laplacian(gray, cv2.CV_64F).var()

        # 1:1 Pixel Center Crop Mode
        if zoom_enabled:
            cw, ch = w // 2, h // 2
            crop_size = 350
            frame = frame[ch - crop_size : ch + crop_size, cw - crop_size : cw + crop_size]
            gray = gray[ch - crop_size : ch + crop_size, cw - crop_size : cw + crop_size]

        # Edge Peaking Overlay (Cyan highlights on in-focus edges)
        if peaking_enabled:
            edges = cv2.Canny(gray, 60, 150)
            frame[edges > 0] = [255, 255, 0]

        # Leveling & Horizon Alignment Reticle
        if grid_enabled:
            fh, fw = frame.shape[:2]
            cx, cy = fw // 2, fh // 2
            cv2.line(frame, (cx, 0), (cx, fh), (0, 0, 255), 1)
            cv2.line(frame, (0, cy), (fw, cy), (0, 0, 255), 1)
            cv2.circle(frame, (cx, cy), 40, (0, 255, 255), 1)
            cv2.circle(frame, (cx, cy), 150, (0, 255, 255), 1)

        # Telemetry Banner
        hud = [
            f"Sensor Native: {w}x{h} | Focus Sharpness: {focus_score:.1f}",
            f"Focus Peaking [F]: {'ON' if peaking_enabled else 'OFF'}",
            f"1:1 Center Zoom [Z]: {'ON' if zoom_enabled else 'OFF'}",
            f"Alignment Grid [G]: {'ON' if grid_enabled else 'OFF'}",
        ]

        overlay = frame.copy()
        cv2.rectangle(overlay, (10, 10), (450, 115), (0, 0, 0), -1)
        frame = cv2.addWeighted(overlay, 0.65, frame, 0.35, 0)

        for idx, text in enumerate(hud):
            cv2.putText(frame, text, (15, 30 + idx * 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)

        # Scale for clean viewing on laptop screen
        display = cv2.resize(frame, (1024, 768)) if not zoom_enabled else frame
        cv2.imshow("Manual Lens Alignment & Focus", display)

        key = cv2.waitKey(1) & 0xFF
        if key in (27, ord('q')):
            break
        elif key in (ord('f'), ord('F')):
            peaking_enabled = not peaking_enabled
        elif key in (ord('z'), ord('Z')):
            zoom_enabled = not zoom_enabled
        elif key in (ord('g'), ord('G')):
            grid_enabled = not grid_enabled

    cap.stopped = True
    cv2.destroyAllWindows()

if __name__ == '__main__':
    main()