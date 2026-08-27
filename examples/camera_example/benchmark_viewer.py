import cv2
import urllib.request
import numpy as np
import time
import threading
from collections import deque

ESP32_IP = "10.42.0.153"
STREAM_URL = f"http://{ESP32_IP}/"

class VideoStreamReceiver:
    def __init__(self, url):
        self.url = url
        self.latest_frame = None
        self.frame_size_bytes = 0
        self.current_lat_ms = 0.0
        self.frame_times = deque()
        self.frame_sizes = deque()
        self.latencies = deque()
        self.stopped = False
        self.lock = threading.Lock()
        
        self.thread = threading.Thread(target=self._stream_reader, daemon=True)
        self.thread.start()

    def _stream_reader(self):
        while not self.stopped:
            try:
                stream = urllib.request.urlopen(self.url, timeout=3)
                buffer = bytearray()
                t_prev = time.time()

                while not self.stopped:
                    chunk = stream.read(4096)
                    if not chunk:
                        break
                    buffer.extend(chunk)

                    a = buffer.find(b'\xff\xd8')
                    b = buffer.find(b'\xff\xd9')

                    if a != -1 and b != -1 and b > a:
                        jpg_bytes = bytes(buffer[a:b+2])
                        buffer = buffer[b+2:]

                        if len(jpg_bytes) < 500:
                            continue

                        t_now = time.time()
                        lat = (t_now - t_prev) * 1000.0
                        t_prev = t_now

                        np_arr = np.frombuffer(jpg_bytes, dtype=np.uint8)
                        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

                        if frame is not None and frame.size > 0:
                            with self.lock:
                                self.latest_frame = frame
                                self.frame_size_bytes = len(jpg_bytes)
                                self.current_lat_ms = lat
                                self.frame_times.append(t_now)
                                self.frame_sizes.append((t_now, len(jpg_bytes)))
                                self.latencies.append((t_now, lat))

                                cutoff = t_now - 16.0
                                while self.frame_times and self.frame_times[0] < cutoff:
                                    self.frame_times.popleft()
                                while self.frame_sizes and self.frame_sizes[0][0] < cutoff:
                                    self.frame_sizes.popleft()
                                while self.latencies and self.latencies[0][0] < cutoff:
                                    self.latencies.popleft()
            except Exception:
                time.sleep(0.5)

    def get_frame_and_stats(self):
        with self.lock:
            if self.latest_frame is None:
                return None, None
            
            now = time.time()
            def calc_window(sec):
                cutoff = now - sec
                times = [t for t in self.frame_times if t >= cutoff]
                sizes = [s for t, s in self.frame_sizes if t >= cutoff]
                lats = [l for t, l in self.latencies if t >= cutoff]

                count = len(times)
                if count < 2:
                    return {"fps": 0.0, "kbps": 0.0, "avg_kb": 0.0, "avg_lat": 0.0}

                elapsed = times[-1] - times[0]
                fps = (count - 1) / elapsed if elapsed > 0 else 0.0
                total_kb = sum(sizes) / 1024.0
                return {
                    "fps": fps,
                    "kbps": (total_kb * 8.0) / elapsed if elapsed > 0 else 0.0,
                    "avg_kb": total_kb / count if count > 0 else 0.0,
                    "avg_lat": sum(lats) / len(lats) if lats else 0.0
                }

            stats = {
                "cur_size": float(self.frame_size_bytes),
                "cur_lat": float(self.current_lat_ms),
                "s5": calc_window(5),
                "s10": calc_window(10),
                "s15": calc_window(15)
            }
            return self.latest_frame.copy(), stats

    def stop(self):
        self.stopped = True

def main():
    print(f"Connecting threaded stream to {STREAM_URL}...")
    receiver = VideoStreamReceiver(STREAM_URL)
    cv2.namedWindow("ESP32 Rover Telemetry", cv2.WINDOW_NORMAL)
    last_print = time.time()

    while True:
        frame, stats = receiver.get_frame_and_stats()

        if frame is not None and stats is not None:
            h, w = frame.shape[:2]
            s5, s10, s15 = stats["s5"], stats["s10"], stats["s15"]

            hud = [
                f"Res: {w}x{h} | Cur Frame: {stats['cur_size']/1024:.1f} KB | Frame Lat: {stats['cur_lat']:.1f} ms",
                f"FPS       -> 5s: {s5['fps']:4.1f} | 10s: {s10['fps']:4.1f} | 15s: {s15['fps']:4.1f}",
                f"Latency   -> 5s: {s5['avg_lat']:4.1f}ms| 10s: {s10['avg_lat']:4.1f}ms| 15s: {s15['avg_lat']:4.1f}ms",
                f"Payload   -> 5s: {s5['avg_kb']:4.1f}KB| 10s: {s10['avg_kb']:4.1f}KB| 15s: {s15['avg_kb']:4.1f}KB",
                f"Bandwidth -> 5s: {s5['kbps']:5.0f}k | 10s: {s10['kbps']:5.0f}k | 15s: {s15['kbps']:5.0f}k",
            ]

            overlay = frame.copy()
            cv2.rectangle(overlay, (5, 5), (460, 115), (0, 0, 0), -1)
            frame = cv2.addWeighted(overlay, 0.65, frame, 0.35, 0)

            for idx, text in enumerate(hud):
                cv2.putText(frame, text, (10, 22 + idx * 18),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 255, 0), 1, cv2.LINE_AA)

            cv2.imshow("ESP32 Rover Telemetry", frame)

            if time.time() - last_print >= 5.0:
                last_print = time.time()
                print(f"[{w}x{h}] 15s Summary: {s15['fps']:.1f} FPS | Lat: {s15['avg_lat']:.1f}ms | {s15['avg_kb']:.1f} KB/f | {s15['kbps']:.0f} kbps")
        else:
            time.sleep(0.01)

        if cv2.waitKey(1) & 0xFF in (27, ord('q')):
            break

    receiver.stop()
    cv2.destroyAllWindows()

if __name__ == '__main__':
    main()