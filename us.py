import socket
import threading
import queue
import cv2
import numpy as np
import time

class USProbe:
    def __init__(self, ip='192.168.1.1', data_port=5002, info_port=5003):
        self.ip = ip
        self.data_port = data_port
        self.info_port = info_port
        
        self.info_socket = None
        self.data_socket = None
        
        self.frame_queue = queue.Queue(maxsize=2)
        
        self.is_streaming = False
        self._threads_running = False
        
        # State tracking for UI overlay
        self.current_depth_level = 1
        self.current_gain = 60
        self.current_dr = 60
        self.current_frequency = 3.2
        self.mode = 'curved' # 'curved' or 'linear'
        self.is_frozen = True
        self.ignore_unexpected_freeze_until = 0.0
        
        # Hardware specific constants
        self.MAGIC = b'\x5a\xa5\xff\x00\x5a\xa5\xff\x00'
        self.SCANLINE_PACKET_SIZE = 517
        self.SCANLINES_PER_FRAME = 128
        self.SAMPLES_PER_LINE = 512
        
        # Precompute warp maps
        self.map_x, self.map_y = self._init_scan_conversion(self.SCANLINES_PER_FRAME, self.SAMPLES_PER_LINE, angle_deg=60, r_min=150)

    def _init_scan_conversion(self, num_lines, num_samples, angle_deg=60, r_min=150):
        angle_rad = np.deg2rad(angle_deg)
        r_max = r_min + num_samples
        
        out_w = int(2 * r_max * np.sin(angle_rad / 2)) + 40
        out_h = int(r_max + 20 - r_min)
        
        cx = out_w / 2
        cy = 20 - r_min
        
        X, Y = np.meshgrid(np.arange(out_w), np.arange(out_h))
        
        R = np.sqrt((X - cx)**2 + (Y - cy)**2)
        Theta = np.arctan2(X - cx, Y - cy) 
        
        map_y = R - r_min 
        map_x = (Theta + angle_rad / 2) / angle_rad * (num_lines - 1)
        
        invalid = (R < r_min) | (R > r_max) | (Theta < -angle_rad / 2) | (Theta > angle_rad / 2)
        map_x[invalid] = -1
        map_y[invalid] = -1
        
        return map_x.astype(np.float32), map_y.astype(np.float32)

    def initiate(self):
        """Connects to the probe and starts background threads."""
        self._threads_running = True
        
        t1 = threading.Thread(target=self._monitor_info_port)
        t1.daemon = True
        t1.start()
        
        t2 = threading.Thread(target=self._monitor_data_port)
        t2.daemon = True
        t2.start()

    def disconnect(self):
        self._threads_running = False
        if self.info_socket:
            try:
                self.info_socket.close()
            except:
                pass
        if self.data_socket:
            try:
                self.data_socket.close()
            except:
                pass

    def _monitor_info_port(self):
        print(f"Connecting to info port {self.info_port}...")
        try:
            self.info_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.info_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.info_socket.connect((self.ip, self.info_port))
            print(f"Connected to info port {self.info_port}!")
            
            # Start stream automatically
            self.unfreeze()
            
            buffer = b''
            while self._threads_running:
                data = self.info_socket.recv(1024)
                if not data:
                    break
                    
                buffer += data
                
                # The probe constantly sends status packets starting with 5a a5.
                while True:
                    idx = buffer.find(b'\x5a\xa5')
                    if idx == -1:
                        # Keep last byte just in case it's the 5a of a split 5aa5
                        buffer = buffer[-1:] if len(buffer) > 0 else b''
                        break
                        
                    if len(buffer) >= idx + 4:
                        status_byte = buffer[idx + 2]
                        mode_byte = buffer[idx + 3]
                        
                        # Sync state if probe naturally responds
                        if mode_byte == 0x50:
                            self.mode = 'curved'
                        elif mode_byte == 0x1e:
                            self.mode = 'linear'
                            
                        # Only check for physical button triggers if we aren't currently waiting for a command to process
                        if time.time() > self.ignore_unexpected_freeze_until:
                            probe_is_frozen = (status_byte == 0x02)
                            
                            # The physical button on the probe toggles the probe into a freeze state (0x02)
                            # When this happens unexpectedly, we must actively command the probe to switch heads.
                            if probe_is_frozen and not self.is_frozen:
                                print(f"Physical button press detected! Probe was {self.mode}, commanding switch...")
                                self.is_frozen = True
                                
                                # Toggle to the other mode
                                if self.mode == 'curved':
                                    print("Sending Linear switch command...")
                                    self._send_command(b'\x5a\xa5\x32\x9e') # Command linear
                                else:
                                    print("Sending Curved switch command...")
                                    self._send_command(b'\x5a\xa5\x22\xd0') # Command curved
                                    
                                time.sleep(0.5)
                                self.unfreeze()
                            else:
                                self.is_frozen = probe_is_frozen
                                
                        # Advance buffer past this header
                        buffer = buffer[idx + 4:]
                    else:
                        # Not enough bytes to read the mode byte yet
                        buffer = buffer[idx:]
                        break
                        
        except Exception as e:
            print(f"Info port error: {e}")
        finally:
            self.info_socket = None

    def _monitor_data_port(self):
        print(f"Connecting to data port {self.data_port}...")
        try:
            self.data_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.data_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.data_socket.connect((self.ip, self.data_port))
            print(f"Connected to data port {self.data_port}!")
            
            buffer = b''
            scanlines = []   # accumulates scanlines for current frame

            while self._threads_running:
                data = self.data_socket.recv(65536)
                if not data:
                    break

                buffer += data

                while True:
                    magic_idx = buffer.find(self.MAGIC)

                    if magic_idx == -1:
                        break

                    if len(buffer) < magic_idx + len(self.MAGIC) + self.SCANLINE_PACKET_SIZE:
                        break

                    packet_start = magic_idx + len(self.MAGIC)
                    packet_data = buffer[packet_start:packet_start + self.SCANLINE_PACKET_SIZE]

                    # Header byte[1] is the scanline index (0–127).
                    # When it resets to 0, we're at the true start of a new frame.
                    # Any scanlines collected before seeing index 0 are a mid-connection
                    # fragment and must be discarded.
                    scanline_idx = packet_data[1]

                    if scanline_idx == 0:
                        # Start of a fresh frame — clear any partial data
                        scanlines = []

                    scanlines.append(np.frombuffer(packet_data[-self.SAMPLES_PER_LINE:], dtype=np.uint8))

                    buffer = buffer[packet_start + self.SCANLINE_PACKET_SIZE:]

                    # Finalise frame when we reach the last scanline
                    if scanline_idx == self.SCANLINES_PER_FRAME - 1 and len(scanlines) == self.SCANLINES_PER_FRAME:
                        frame = np.array(scanlines, dtype=np.uint8).reshape(
                            (self.SCANLINES_PER_FRAME, self.SAMPLES_PER_LINE))
                        frame = frame.T  # (depth, scanlines) = (512, 128)

                        if self.mode == 'curved':
                            processed_frame = cv2.remap(frame, self.map_x, self.map_y,
                                                        cv2.INTER_LINEAR,
                                                        borderMode=cv2.BORDER_CONSTANT,
                                                        borderValue=0)
                        else:
                            processed_frame = cv2.resize(frame, (512, 600),
                                                         interpolation=cv2.INTER_LINEAR)
                            canvas = np.zeros((600, 800), dtype=np.uint8)
                            x_offset = (800 - 512) // 2
                            canvas[0:600, x_offset:x_offset + 512] = processed_frame
                            processed_frame = canvas

                        if not self.frame_queue.full():
                            self.frame_queue.put(processed_frame)

                        scanlines = []

                        
        except Exception as e:
            print(f"Data port error: {e}")

    def _send_command(self, cmd_bytes):
        if self.info_socket:
            try:
                self.info_socket.send(cmd_bytes)
            except Exception as e:
                print(f"Failed to send command: {e}")
        else:
            print("Cannot send command, info socket not connected.")

    # --- Probe Commands ---

    def unfreeze(self):
        cmd = b'\x5a\xa5\x81\x50' if self.mode == 'curved' else b'\x5a\xa5\x81\x1e'
        self._send_command(cmd)
        self.is_frozen = False
        self.ignore_unexpected_freeze_until = time.time() + 1.5

    def freeze(self):
        cmd = b'\x5a\xa5\x01\x50' if self.mode == 'curved' else b'\x5a\xa5\x01\x1e'
        self._send_command(cmd)
        self.is_frozen = True
        self.ignore_unexpected_freeze_until = time.time() + 1.5

    def toggle_mode(self):
        """Manually toggle between curved and linear modes."""
        self.freeze()
        if self.mode == 'curved':
            print("Software switch: Changing to Linear Mode...")
            self._send_command(b'\x5a\xa5\x32\x9e')
            self.mode = 'linear'
        else:
            print("Software switch: Changing to Curved Mode...")
            self._send_command(b'\x5a\xa5\x22\xd0')
            self.mode = 'curved'
        
        time.sleep(0.5)
        self.unfreeze()

    def set_depth(self, level):
        """Valid levels are 1, 2, 3, 4"""
        if level < 1 or level > 4:
            return
            
        cmds_curved = {
            1: b'\x5a\xa5\xb0\x50',
            2: b'\x5a\xa5\xb1\x50',
            3: b'\x5a\xa5\xb2\x50',
            4: b'\x5a\xa5\xb3\x50'
        }
        cmds_linear = {
            1: b'\x5a\xa5\xb0\x1e',
            2: b'\x5a\xa5\xb1\x1e',
            3: b'\x5a\xa5\xb2\x1e',
            4: b'\x5a\xa5\xb3\x1e'
        }
        cmd = cmds_curved[level] if self.mode == 'curved' else cmds_linear[level]
        self._send_command(cmd)
        self.current_depth_level = level

    def set_gain(self, gain):
        """Gain from 30 to 105"""
        gain = max(30, min(105, int(gain))) # Clamp
        
        # 1. Send 4-byte prefix
        prefix_cmd = bytes([0x5a, 0xa5, 0xa0, gain])
        self._send_command(prefix_cmd)
        
        # 2. Briefly pause to ensure TCP packet separation
        time.sleep(0.01)
        
        # 3. Send 20-byte commit payload
        static_suffix = b'\x58\x85\x00\x00\x28\x40\x03\x00\x00\x50\x00\x03\x22\x5e\x00\xe5'
        commit_cmd = prefix_cmd + static_suffix
        self._send_command(commit_cmd)
        
        self.current_gain = gain

    def set_dynamic_range(self, dr):
        """Valid values: 40, 50, 60, 70, 80, 90, 100, 110"""
        dr_chksm = {
            40: (0x28, 0x83), 50: (0x32, 0x79), 60: (0x3c, 0x6f), 70: (0x46, 0x65),
            80: (0x50, 0x5b), 90: (0x5a, 0x51), 100: (0x64, 0x47), 110: (0x6e, 0x3d)
        }
        if dr not in dr_chksm:
            return
            
        val, chksm = dr_chksm[dr]
        cmd = b'\x5a\xa5\xa1\x51\x5f\xf5\x00\x00\x00\x00' + bytes([val]) + b'\x00\x00\x00\x00\x01\x00\x00\x00' + bytes([chksm])
        self._send_command(cmd)
        self.current_dr = dr

    def set_frequency(self, freq):
        """
        Valid frequencies:
        Curved: 3.2, 5.0
        Linear: 7.5, 10.0 (H10)
        """
        if freq == 3.2 and self.mode == 'curved':
            self._send_command(b'\x5a\xa5\xa1\x51\x5f\xf5\x00\x00\x00\x00\x32\x00\x00\x00\x01\x00\x00\x00\x00\x79')
            self.current_frequency = freq
        elif freq == 5.0 and self.mode == 'curved':
            self._send_command(b'\x5a\xa5\xa1\x51\x5f\xf5\x00\x00\x00\x00\x32\x00\x00\x00\x00\x00\x00\x00\x00\x7a')
            self.current_frequency = freq
        elif freq == 7.5 and self.mode == 'linear':
            self._send_command(b'\x5a\xa5\xa1\x51\x5f\xf5\x00\x00\x00\x00\x32\x00\x00\x00\x01\x00\x00\x00\x00\x79')
            self.current_frequency = freq
        elif freq == 10.0 and self.mode == 'linear':
            self._send_command(b'\x5a\xa5\xa1\x51\x5f\xf5\x00\x00\x00\x00\x32\x00\x00\x00\x00\x00\x00\x00\x00\x7a')
            self.current_frequency = freq
        else:
            print(f"Invalid or incompatible frequency {freq} for mode {self.mode}")

    def get_latest_frame(self):
        """Returns the latest B-mode frame (curved), or None if queue is empty."""
        try:
            return self.frame_queue.get_nowait()
        except queue.Empty:
            return None

    def launch_live_window(self):
        """Launches a live OpenCV window with keyboard controls to change probe parameters."""
        self.initiate()
        
        print("UI Loop started.")
        print("Controls:")
        print("  's' - Start Stream (Unfreeze)")
        print("  'f' - Stop Stream (Freeze)")
        print("  'm' - Toggle Mode (Curved / Linear)")
        print("  '6' to '9' - Change Depth levels (1 to 4)")
        print("  '[' / ']' - Decrease / Increase Gain (30 db to 105)")
        print("  'q', 'w', 'e', 'r', 't', 'y', 'u', 'i' - Change DR levels (40 to 110)")
        print("  'c', 'v' - Change Frequency (Curved: 3.2/H5.0, Linear: 7.5/H10.0)")
        print("  'x' - Quit")
        
        cv2.namedWindow('Live Ultrasound', cv2.WINDOW_NORMAL)
        
        # Map for DR keys
        dr_keys = {
            ord('q'): 40, ord('w'): 50, ord('e'): 60, ord('r'): 70,
            ord('t'): 80, ord('y'): 90, ord('u'): 100, ord('i'): 110
        }

        cached_frame = np.zeros((600, 800), dtype=np.uint8)
        
        while True:
            # Get frame if available
            new_frame = self.get_latest_frame()
            if new_frame is not None:
                cached_frame = new_frame.copy()
                
            # Create a display frame to overlay text on
            display_frame = cached_frame.copy()
            
            # Add overlay text
            if self.is_frozen:
                cv2.putText(display_frame, "FROZEN - Press 's' to Unfreeze", (50, 50), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255,), 2)
            else:
                if self.current_frequency == 10.0:
                    freq_str = "H10.0"
                elif self.current_frequency == 5.0:
                    freq_str = "H5.0"
                else:
                    freq_str = str(self.current_frequency)
                info_text = f"Mode: {self.mode.upper()} | Depth: L{self.current_depth_level} | Gain: {self.current_gain} | DR: {self.current_dr} | Freq: {freq_str}MHz"
                cv2.putText(display_frame, info_text, (20, 30), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,), 2)
                
            cv2.imshow('Live Ultrasound', display_frame)

            key = cv2.waitKey(30) & 0xFF
            
            if key == ord('x'):
                break
            elif key == ord('s'):
                print("Starting stream (Unfreeze)...")
                self.unfreeze()
            elif key == ord('f'):
                print("Stopping stream (Freeze)...")
                self.freeze()
            elif key in [ord('6'), ord('7'), ord('8'), ord('9')]:
                level = key - ord('6') + 1  # 6 -> 1, 7 -> 2, 8 -> 3, 9 -> 4
                print(f"Changing Depth Level to {level}")
                self.set_depth(level)
            elif key in dr_keys:
                dr_val = dr_keys[key]
                print(f"Changing DR to {dr_val}")
                self.set_dynamic_range(dr_val)
            elif key in [ord('c'), ord('v')]:
                if key == ord('c'):
                    if self.mode == 'curved':
                        self.set_frequency(3.2)
                    else:
                        self.set_frequency(7.5)
                elif key == ord('v'):
                    if self.mode == 'curved':
                        self.set_frequency(5.0)
                    else:
                        self.set_frequency(10.0)
            elif key == ord('m'):
                self.toggle_mode()
                # Update UI explicitly to default mode frequencies
                if self.mode == 'curved':
                    self.current_frequency = 3.2
                else:
                    self.current_frequency = 7.5
            elif key == ord('['):
                new_gain = max(30, self.current_gain - 1)
                print(f"Decreasing Gain to {new_gain}")
                self.set_gain(new_gain)
            elif key == ord(']'):
                new_gain = min(105, self.current_gain + 1)
                print(f"Increasing Gain to {new_gain}")
                self.set_gain(new_gain)

        self.disconnect()
        cv2.destroyAllWindows()
