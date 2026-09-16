import cv2
import socket
import numpy as np
import time
import threading
from collections import deque

ESP32_IP = "10.42.0.153"
ESP32_PORT = 80

class InstantFrameClient:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.latest_jpg = None
        self.cur_lat = 0.0
        self.cur_size = 0.0
        self.stopped = False
        self.lock = threading.Lock()

        # Rolling telemetry deques (timestamp, value)
        self.frame_times = deque()
        self.frame_sizes = deque()
        self.latencies = deque()

        self.thread = threading.Thread(target=self._recv_loop, daemon=True)
        self.thread.start()

    def _connect_socket(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2.5)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 32768)
        s.connect((self.host, self.port))
        req = (
            f"GET / HTTP/1.1\r\n"
            f"Host: {self.host}\r\n"
            f"Connection: close\r\n\r\n"
        )
        s.sendall(req.encode('ascii'))
        return s

    def _recv_loop(self):
        buf = bytearray()
        t_prev = time.time()

        while not self.stopped:
            try:
                s = self._connect_socket()
                while not self.stopped:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    buf.extend(chunk)

                    # Extract all complete JPEGs and keep only the latest one
                    newest_jpg = None
                    while True:
                        a = buf.find(b'\xff\xd8')
                        if a == -1:
                            if len(buf) > 32768:
                                buf = buf[-4096:]
                            break

                        b = buf.find(b'\xff\xd9', a + 2)
                        if b == -1:
                            if a > 0:
                                buf = buf[a:]
                            break

                        extracted = bytes(buf[a:b+2])
                        buf = buf[b+2:]

                        if len(extracted) > 1000:
                            newest_jpg = extracted

                    if newest_jpg is not None:
                        t_now = time.time()
                        frame_gap_ms = (t_now - t_prev) * 1000.0
                        t_prev = t_now

                        with self.lock:
                            self.latest_jpg = newest_jpg
                            self.cur_lat = frame_gap_ms
                            self.cur_size = len(newest_jpg)
                            self.frame_times.append(t_now)
                            self.frame_sizes.append((t_now, len(newest_jpg)))
                            self.latencies.append((t_now, frame_gap_ms))

                            # Prune records older than 16 seconds
                            cutoff = t_now - 16.0
                            while self.frame_times and self.frame_times[0] < cutoff:
                                self.frame_times.popleft()
                            while self.frame_sizes and self.frame_sizes[0][0] < cutoff:
                                self.frame_sizes.popleft()
                            while self.latencies and self.latencies[0][0] < cutoff:
                                self.latencies.popleft()

                s.close()
            except Exception:
                time.sleep(0.2)

    def get_stats_window(self, sec):
        now = time.time()
        cutoff = now - sec
        with self.lock:
            times = [t for t in self.frame_times if t >= cutoff]
            sizes = [s for t, s in self.frame_sizes if t >= cutoff]
            lats = [l for t, l in self.latencies if t >= cutoff]

            count = len(times)
            if count < 2:
                return {
                    "fps": 0.0, "kbps": 0.0, "avg_kb": 0.0, 
                    "avg_lat": 0.0, "max_gap": 0.0, "stutters": 0
                }

            elapsed = times[-1] - times[0]
            fps = (count - 1) / elapsed if elapsed > 0 else 0.0
            total_kb = sum(sizes) / 1024.0
            
            # Stutter metrics
            max_gap = max(lats) if lats else 0.0
            stutters_count = sum(1 for l in lats if l > 80.0)

            return {
                "fps": fps,
                "kbps": (total_kb * 8.0) / elapsed if elapsed > 0 else 0.0,
                "avg_kb": total_kb / count if count > 0 else 0.0,
                "avg_lat": sum(lats) / len(lats) if lats else 0.0,
                "max_gap": max_gap,
                "stutters": stutters_count
            }

    def fetch_latest(self):
        with self.lock:
            if self.latest_jpg is None:
                return None, 0.0, 0.0
            jpg = self.latest_jpg
            lat = self.cur_lat
            size = self.cur_size
            self.latest_jpg = None
            return jpg, lat, size

    def stop(self):
        self.stopped = True


def main():
    print(f"Connecting to {ESP32_IP}:{ESP32_PORT} (Telemetry with Stutter Detection)...")
    client = InstantFrameClient(ESP32_IP, ESP32_PORT)
    cv2.namedWindow("ESP32 Rover Telemetry", cv2.WINDOW_NORMAL)
    last_print = time.time()

    while True:
        jpg_bytes, cur_lat, cur_size = client.fetch_latest()

        if jpg_bytes is not None:
            np_arr = np.frombuffer(jpg_bytes, dtype=np.uint8)
            frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

            if frame is not None and frame.size > 0:
                s5 = client.get_stats_window(5)
                s10 = client.get_stats_window(10)
                s15 = client.get_stats_window(15)

                h, w = frame.shape[:2]

                hud = [
                    f"Res: {w}x{h} | Cur Frame: {cur_size/1024:.1f} KB | Frame Gap: {cur_lat:.1f} ms",
                    f"FPS        -> 5s: {s5['fps']:4.1f} | 10s: {s10['fps']:4.1f} | 15s: {s15['fps']:4.1f}",
                    f"Avg Gap    -> 5s: {s5['avg_lat']:4.1f}ms| 10s: {s10['avg_lat']:4.1f}ms| 15s: {s15['avg_lat']:4.1f}ms",
                    f"Max Freeze -> 5s: {s5['max_gap']:4.0f}ms| 10s: {s10['max_gap']:4.0f}ms| 15s: {s15['max_gap']:4.0f}ms",
                    f"Stutters>80-> 5s: {s5['stutters']:4d}   | 10s: {s10['stutters']:4d}   | 15s: {s15['stutters']:4d}",
                    f"Bandwidth  -> 5s: {s5['kbps']:5.0f}k | 10s: {s10['kbps']:5.0f}k | 15s: {s15['kbps']:5.0f}k",
                ]

                overlay = frame.copy()
                cv2.rectangle(overlay, (5, 5), (480, 135), (0, 0, 0), -1)
                frame = cv2.addWeighted(overlay, 0.65, frame, 0.35, 0)

                for idx, text in enumerate(hud):
                    color = (0, 255, 0)
                    # Turn HUD warning red if recent freeze exceeded 150ms
                    if "Max Freeze" in text and s5['max_gap'] > 150.0:
                        color = (0, 0, 255)
                    cv2.putText(frame, text, (10, 20 + idx * 18),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.40, color, 1, cv2.LINE_AA)

                cv2.imshow("ESP32 Rover Telemetry", frame)

                if time.time() - last_print >= 5.0:
                    last_print = time.time()
                    print(f"[{w}x{h}] 15s: {s15['fps']:.1f} FPS | Max Freeze: {s15['max_gap']:.0f}ms | Drops>80ms: {s15['stutters']} | {s15['kbps']:.0f} kbps")
        else:
            time.sleep(0.001)

        key = cv2.waitKey(1) & 0xFF
        if key in (27, ord('q')):
            break

    client.stop()
    cv2.destroyAllWindows()


if __name__ == '__main__':
    main()