# record_gaze_OBS.py
# =============================================================================
# UPDATES:
# - OBS settings moved to 'obs_config.txt'
# - Loads calibration from 'tobii_calibration.pkl' automatically if found
# =============================================================================

import sys
import os
import tkinter as tk
from tkinter import filedialog, messagebox
import time
import csv
import threading
import pickle
from datetime import datetime
import numpy as np
import sounddevice as sd

try:
    import tobii_research
except ImportError:
    print("ERROR: Tobii Research SDK not found.")
    print("Please download and install the Tobii Pro SDK from:")
    print("  https://www.tobiipro.com/product-listing/tobii-pro-sdk/")
    print("See README.md for full installation instructions.")
    sys.exit(1)

# --- Load or Generate Config ---
CONFIG_FILE = "obs_config.txt"
DEFAULT_CONFIG = {
    "OBS_HOST": "localhost",
    "OBS_PORT": "4455",
    "OBS_PASSWORD": "CHANGE_ME",
    "OBS_SCENE_NAME": "Scene",
    "OBS_VISUAL_SOURCE": "visual_marker",
    "OBS_AUDIO_SOURCE": "Desktop Audio"
}

config = dict(DEFAULT_CONFIG)

OBS_HOST = DEFAULT_CONFIG["OBS_HOST"]
OBS_PORT = int(DEFAULT_CONFIG["OBS_PORT"])
OBS_PASSWORD = DEFAULT_CONFIG["OBS_PASSWORD"]
OBS_SCENE_NAME = DEFAULT_CONFIG["OBS_SCENE_NAME"]
OBS_VISUAL_SOURCE = DEFAULT_CONFIG["OBS_VISUAL_SOURCE"]
OBS_AUDIO_SOURCE = DEFAULT_CONFIG["OBS_AUDIO_SOURCE"]


def load_config():
    """Load obs_config.txt. Create it with defaults if missing; do not exit."""
    global config, OBS_HOST, OBS_PORT, OBS_PASSWORD
    global OBS_SCENE_NAME, OBS_VISUAL_SOURCE, OBS_AUDIO_SOURCE

    if not os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "w") as f:
                for k, v in DEFAULT_CONFIG.items():
                    f.write(f"{k}={v}\n")
            print(f"Created '{CONFIG_FILE}' with default OBS settings.")
            print("Edit it if you use OBS (password, scene, audio source).")
            print("If you are not using OBS, you can leave the defaults.")
        except Exception as e:
            print(f"Error creating config: {e}")

    config = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE, "r") as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    config[k.strip()] = v.strip()
    except Exception as e:
        print(f"Error reading config: {e}")

    OBS_HOST = config.get("OBS_HOST", DEFAULT_CONFIG["OBS_HOST"])
    try:
        OBS_PORT = int(config.get("OBS_PORT", DEFAULT_CONFIG["OBS_PORT"]))
    except ValueError:
        print("WARNING: Invalid OBS_PORT in config; using 4455.")
        OBS_PORT = 4455
    OBS_PASSWORD = config.get("OBS_PASSWORD", "")
    OBS_SCENE_NAME = config.get("OBS_SCENE_NAME", DEFAULT_CONFIG["OBS_SCENE_NAME"])
    OBS_VISUAL_SOURCE = config.get("OBS_VISUAL_SOURCE", DEFAULT_CONFIG["OBS_VISUAL_SOURCE"])
    OBS_AUDIO_SOURCE = config.get("OBS_AUDIO_SOURCE", DEFAULT_CONFIG["OBS_AUDIO_SOURCE"])

# Import OBS WebSocket library
try:
    from obswebsocket import obsws, requests
    OBS_AVAILABLE = True
except ImportError:
    print("WARNING: OBS WebSocket library not found.")
    print("  OBS synchronization will be disabled.")
    print("  To enable it, run: pip install obs-websocket-py")
    OBS_AVAILABLE = False


class OBSController:
    def __init__(self, host=None, port=None, password=None):
        self.ws = None
        self.connected = False
        self.lock = threading.Lock()
        self.host = OBS_HOST if host is None else host
        self.port = OBS_PORT if port is None else port
        self.password = OBS_PASSWORD if password is None else password

    def connect(self):
        if not OBS_AVAILABLE:
            print("OBS WebSocket not available - synchronization disabled")
            return False
        try:
            self.ws = obsws(self.host, self.port, self.password)
            self.ws.connect()
            self.connected = True
            print("Connected to OBS WebSocket")
            return True
        except Exception as e:
            self.connected = False
            print(f"Could not connect to OBS WebSocket: {e}")
            print("  Recording will continue without OBS sync.")
            return False

    def disconnect(self):
        if self.connected and self.ws:
            try:
                self.ws.disconnect()
                self.connected = False
                print("Disconnected from OBS WebSocket")
            except Exception as e:
                print(f"Error disconnecting from OBS: {e}")

    def _get_scene_item_id(self, source_name):
        try:
            scene_items = self.ws.call(requests.GetSceneItemList(sceneName=OBS_SCENE_NAME))
            for item in scene_items.getSceneItems():
                if item["sourceName"] == source_name:
                    return item["sceneItemId"]
        except Exception as e:
            print(f"Error getting scene item ID for {source_name}: {e}")
        return None

    def send_visual_marker(self, text):
        if not self.connected:
            return False
        with self.lock:
            try:
                self.ws.call(
                    requests.SetInputSettings(
                        inputName=OBS_VISUAL_SOURCE,
                        inputSettings={"text": text},
                    )
                )
                item_id = self._get_scene_item_id(OBS_VISUAL_SOURCE)
                if item_id is not None:
                    self.ws.call(
                        requests.SetSceneItemEnabled(
                            sceneName=OBS_SCENE_NAME,
                            sceneItemId=item_id,
                            sceneItemEnabled=True,
                        )
                    )
                return True
            except Exception as e:
                print(f"Error sending visual marker: {e}")
                return False

    def hide_visual_marker(self):
        if not self.connected:
            return False
        with self.lock:
            try:
                item_id = self._get_scene_item_id(OBS_VISUAL_SOURCE)
                if item_id is not None:
                    self.ws.call(
                        requests.SetSceneItemEnabled(
                            sceneName=OBS_SCENE_NAME,
                            sceneItemId=item_id,
                            sceneItemEnabled=False,
                        )
                    )
                return True
            except Exception as e:
                print(f"Error hiding visual marker: {e}")
                return False

    def trigger_audio_marker(self):
        if not self.connected:
            return False
        with self.lock:
            try:
                self.ws.call(
                    requests.TriggerMediaInputAction(
                        inputName=OBS_AUDIO_SOURCE,
                        mediaAction="OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PLAY",
                    )
                )
                return True
            except Exception as e:
                print(f"Error triggering audio marker: {e}")
                return False


class AudioPlayer:
    def __init__(self, sample_rate=44100):
        self.sample_rate = sample_rate
        self.current_audio = None
        self.audio_position = 0
        self.stream = None
        self.playing = False
        self.audio_lock = threading.Lock()

    def audio_callback(self, outdata, frames, time_info, status):
        if status:
            print(f"Audio status: {status}")
        with self.audio_lock:
            if self.current_audio is not None and self.audio_position < len(self.current_audio):
                remaining = len(self.current_audio) - self.audio_position
                n = min(frames, remaining)
                outdata[:n, 0] = self.current_audio[self.audio_position : self.audio_position + n]
                if n < frames:
                    outdata[n:, 0] = 0
                self.audio_position += n
                if self.audio_position >= len(self.current_audio):
                    self.current_audio = None
                    self.audio_position = 0
            else:
                outdata.fill(0)

    def start_stream(self):
        try:
            self.stream = sd.OutputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                callback=self.audio_callback,
                blocksize=512,
                latency="low",
            )
            self.stream.start()
            self.playing = True
            return True
        except Exception as e:
            print(f"WARNING: Could not start audio stream: {e}")
            return False

    def play_sound(self, audio_data):
        with self.audio_lock:
            self.current_audio = audio_data.copy()
            self.audio_position = 0

    def stop_stream(self):
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.playing = False


class GazeRecorder:
    def __init__(self, master):
        self.master = master
        master.title("Tobii Gaze Recorder with OBS Sync")
        master.attributes("-fullscreen", True)

        self.canvas = tk.Canvas(master, bg="black", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.width = master.winfo_screenwidth()
        self.height = master.winfo_screenheight()

        self.eye_tracker = None
        self.start_time = None
        self.recording = False
        self.csv_writer = None
        self.csv_file = None
        self.gaze_circle = None
        self.shutdown_flag = threading.Event()

        self.audio_player = AudioPlayer()
        self.audio_initialized = False

        self.obs_controller = OBSController()
        self.obs_connected = self.obs_controller.connect()

        obs_text = "OBS: Connected" if self.obs_connected else "OBS: Not connected (recording continues without sync)"
        self.esc_label = tk.Label(
            master,
            text=f"Recording in progress.  Press ESC or Alt+Q to stop and save.  Press Alt+M to minimize.  |  {obs_text}",
            font=("Helvetica", 13),
            bg="black",
            fg="#cccccc",
        )
        self.esc_label.place(relx=0.5, rely=0.98, anchor="s")
        
        if self._setup_eyetracker():
             self._load_calibration_auto_or_prompt()
             self._setup_csv()
             self._start_recording_logic()

        master.after(200, lambda: master.focus_force())
        master.bind("<FocusIn>", lambda e: master.focus_force())
        master.bind("<Visibility>", lambda e: master.focus_force())
        master.bind("<Escape>", self._exit_app)
        master.bind("<Alt-q>", self._exit_app)
        master.bind("<Alt-Q>", self._exit_app)
        master.bind("<Alt-m>", self._minimize_window)
        master.bind("<Alt-M>", self._minimize_window)
        master.protocol("WM_DELETE_WINDOW", self._exit_app)

    def _setup_csv(self):
        filename = time.strftime("gaze_recording_%Y%m%d_%H%M%S.csv")
        self.csv_file = open(filename, mode="w", newline="")
        self.csv_writer = csv.writer(self.csv_file)

        self.csv_writer.writerow(
            [
                "timestamp_system",
                "timestamp_ms",
                "left_x",
                "left_y",
                "left_pupil",
                "right_x",
                "right_y",
                "right_pupil",
                "left_validity",
                "right_validity",
                "left_gaze_origin_x",
                "left_gaze_origin_y",
                "left_gaze_origin_z",
                "right_gaze_origin_x",
                "right_gaze_origin_y",
                "right_gaze_origin_z",
                "left_gaze_point_3d_x",
                "left_gaze_point_3d_y",
                "left_gaze_point_3d_z",
                "right_gaze_point_3d_x",
                "right_gaze_point_3d_y",
                "right_gaze_point_3d_z",
                "sync_marker",
            ]
        )
        print(f"CSV file created: {filename}")

    def _setup_eyetracker(self):
        found_eyetrackers = tobii_research.find_all_eyetrackers()
        if not found_eyetrackers:
            print("ERROR: No eye trackers found.")
            self._show_message("No eye tracker found. Connect a Tobii device and try again.")
            return False

        self.eye_tracker = found_eyetrackers[0]
        print(f"Found eye tracker: {self.eye_tracker.serial_number}")
        return True

    def _load_calibration_auto_or_prompt(self):
        """Tries to load 'tobii_calibration.pkl' automatically. If not found, prompts user."""
        default_calib = "tobii_calibration.pkl"
        
        # Try automatic load
        if os.path.exists(default_calib):
            try:
                with open(default_calib, "rb") as f:
                    data = pickle.load(f)
                if "calibration_data_binary" in data:
                    self.eye_tracker.apply_calibration_data(data["calibration_data_binary"])
                    print(f"Loaded calibration from {default_calib}")
                    return
            except Exception as e:
                print(f"Failed to load default calibration: {e}")
        
        # If automatic load failed or file missing, prompt user
        ans = messagebox.askyesno("Load Calibration", 
                                  f"Default calibration '{default_calib}' not found or invalid.\n"
                                  "Do you want to browse for a calibration (.pkl) file?")
        if not ans:
            print("Skipping calibration load.")
            return

        filepath = filedialog.askopenfilename(
            title="Select Calibration File",
            filetypes=[("Pickle files", "*.pkl")]
        )

        if not filepath:
            return

        try:
            with open(filepath, "rb") as f:
                data = pickle.load(f)
            
            if "calibration_data_binary" in data:
                print("Applying calibration data...")
                self.eye_tracker.apply_calibration_data(data["calibration_data_binary"])
                print("Calibration applied successfully.")
            else:
                messagebox.showerror("Error", "Selected file does not contain binary calibration data.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load calibration: {e}")

    def _start_recording_logic(self):
        try:
            self.start_time = time.time()

            if self._improved_warmup_audio_device():
                if self.audio_player.start_stream():
                    self.audio_initialized = True
                    print("Audio system initialized successfully")
                else:
                    print("WARNING: Audio callback failed, using fallback method")

            # Trigger START sync markers (both OBS and CSV)
            threading.Thread(
                target=self._trigger_sync_markers, args=("START",), daemon=True
            ).start()

            self.eye_tracker.subscribe_to(
                tobii_research.EYETRACKER_GAZE_DATA,
                self._gaze_data_callback,
                as_dictionary=False,
            )
            self.recording = True
            print("Eye tracking started")
        except Exception as e:
            print(f"ERROR: Could not subscribe to gaze data: {e}")
            self._show_message(f"Error subscribing to gaze data:\n{e}")

    def configure_audio_device(self):
        try:
            sd.default.samplerate = 44100
            sd.default.channels = 1
            sd.default.dtype = "float32"
            sd.default.latency = "low"
            return True
        except Exception as e:
            print(f"WARNING: Audio configuration error: {e}")
            return False

    def _improved_warmup_audio_device(self):
        try:
            if not self.configure_audio_device():
                return False

            if sys.platform == "win32":
                try:
                    import ctypes
                    ctypes.windll.winmm.timeBeginPeriod(1)
                except Exception:
                    pass

            sample_rate = 48000
            warmup_duration = 0.2
            silence = np.zeros(int(warmup_duration * sample_rate), dtype=np.float32)
            sd.play(silence, samplerate=sample_rate, blocking=True)
            time.sleep(0.05)
            return True
        except Exception as e:
            print(f"WARNING: Audio warmup failed: {e}")
            return False

    def _is_valid_position(self, pos):
        return (
            isinstance(pos, tuple)
            and len(pos) == 2
            and all(isinstance(p, (float, int)) for p in pos)
            and 0.0 <= pos[0] <= 1.0
            and 0.0 <= pos[1] <= 1.0
        )

    def _gaze_data_callback(self, gaze_data):
        if not self.recording or self.shutdown_flag.is_set():
            return

        # Initialize all numeric values as NaNs
        left_x = left_y = left_pupil = float("nan")
        right_x = right_y = right_pupil = float("nan")
        left_validity = right_validity = 0
        l_orig_x = l_orig_y = l_orig_z = float("nan")
        r_orig_x = r_orig_y = r_orig_z = float("nan")
        l_pt_x = l_pt_y = l_pt_z = float("nan")
        r_pt_x = r_pt_y = r_pt_z = float("nan")

        def get_coords(obj):
            if obj and len(obj) >= 3:
                return obj[0], obj[1], obj[2]
            return float("nan"), float("nan"), float("nan")

        # Left eye
        if gaze_data.left_eye:
            if gaze_data.left_eye.gaze_point:
                if self._is_valid_position(gaze_data.left_eye.gaze_point.position_on_display_area):
                    left_x, left_y = gaze_data.left_eye.gaze_point.position_on_display_area
                    left_validity = 1

                if hasattr(gaze_data.left_eye.gaze_point, "position_in_user_coordinates"):
                    l_pt_x, l_pt_y, l_pt_z = get_coords(
                        gaze_data.left_eye.gaze_point.position_in_user_coordinates
                    )

            if gaze_data.left_eye.pupil:
                left_pupil = gaze_data.left_eye.pupil.diameter

            if (
                gaze_data.left_eye.gaze_origin
                and hasattr(gaze_data.left_eye.gaze_origin, "position_in_user_coordinates")
            ):
                l_orig_x, l_orig_y, l_orig_z = get_coords(
                    gaze_data.left_eye.gaze_origin.position_in_user_coordinates
                )

        # Right eye
        if gaze_data.right_eye:
            if gaze_data.right_eye.gaze_point:
                if self._is_valid_position(gaze_data.right_eye.gaze_point.position_on_display_area):
                    right_x, right_y = gaze_data.right_eye.gaze_point.position_on_display_area
                    right_validity = 1

                if hasattr(gaze_data.right_eye.gaze_point, "position_in_user_coordinates"):
                    r_pt_x, r_pt_y, r_pt_z = get_coords(
                        gaze_data.right_eye.gaze_point.position_in_user_coordinates
                    )

            if gaze_data.right_eye.pupil:
                right_pupil = gaze_data.right_eye.pupil.diameter

            if (
                gaze_data.right_eye.gaze_origin
                and hasattr(gaze_data.right_eye.gaze_origin, "position_in_user_coordinates")
            ):
                r_orig_x, r_orig_y, r_orig_z = get_coords(
                    gaze_data.right_eye.gaze_origin.position_in_user_coordinates
                )

        # Visual feedback
        valid_xs = [v for v in (left_x, right_x) if not np.isnan(v)]
        valid_ys = [v for v in (left_y, right_y) if not np.isnan(v)]
        if valid_xs and valid_ys:
            avg_x = sum(valid_xs) / len(valid_xs)
            avg_y = sum(valid_ys) / len(valid_ys)
            color = "green" if len(valid_xs) == 2 else "yellow"
            self._draw_gaze_point(avg_x * self.width, avg_y * self.height, color=color)
        else:
            self._clear_gaze_point()

        # Write CSV
        try:
            ts = time.time()
            self.csv_writer.writerow(
                [
                    ts,
                    int((ts - self.start_time) * 1000),
                    left_x,
                    left_y,
                    left_pupil,
                    right_x,
                    right_y,
                    right_pupil,
                    left_validity,
                    right_validity,
                    l_orig_x,
                    l_orig_y,
                    l_orig_z,
                    r_orig_x,
                    r_orig_y,
                    r_orig_z,
                    l_pt_x,
                    l_pt_y,
                    l_pt_z,
                    r_pt_x,
                    r_pt_y,
                    r_pt_z,
                    "",
                ]
            )
        except Exception as e:
            print(f"ERROR: Could not write CSV data: {e}")

    def _draw_gaze_point(self, x, y, color="red"):
        radius = 30
        if self.gaze_circle:
            self.canvas.coords(self.gaze_circle, x - radius, y - radius, x + radius, y + radius)
            self.canvas.itemconfig(self.gaze_circle, fill=color)
        else:
            self.gaze_circle = self.canvas.create_oval(
                x - radius,
                y - radius,
                x + radius,
                y + radius,
                fill=color,
                outline="white",
                width=2,
            )

    def _clear_gaze_point(self):
        if self.gaze_circle:
            self.canvas.delete(self.gaze_circle)
            self.gaze_circle = None

    def _generate_tone_with_envelope(self, frequency, duration, sample_rate=44100):
        t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
        waveform = 0.5 * np.sin(2 * np.pi * frequency * t)
        fade_samples = int(0.005 * sample_rate)
        if len(waveform) > 2 * fade_samples:
            waveform[:fade_samples] *= np.linspace(0, 1, fade_samples)
            waveform[-fade_samples:] *= np.linspace(1, 0, fade_samples)
        return waveform.astype(np.float32)

    def _log_sync_marker(self, marker_type):
        try:
            ts = time.time()
            self.csv_writer.writerow(
                [
                    ts,
                    int((ts - self.start_time) * 1000),
                    "", "", "",
                    "", "", "",
                    "", "",
                    "", "", "",
                    "", "", "",
                    "", "", "",
                    "", "", "",
                    marker_type,
                ]
            )
            self.csv_file.flush()
        except Exception as e:
            print(f"ERROR: Could not log sync marker: {e}")

    def _trigger_sync_markers(self, label):
        if self.shutdown_flag.is_set():
            return
        try:
            sample_rate = 48000
            silence = np.zeros(int(0.2 * sample_rate), dtype=np.float32)
            low_tone = self._generate_tone_with_envelope(400, 0.5, sample_rate)
            high_tone = self._generate_tone_with_envelope(1000, 0.2, sample_rate)
            full_sequence = np.concatenate(
                [silence, low_tone, silence, high_tone, silence, high_tone, silence, high_tone]
            )

            timestamp_str = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            visual_text = f"SYNC_{label}_{timestamp_str}"

            self._log_sync_marker(f"visual_marker_{label}")

            if self.obs_connected:
                self.obs_controller.send_visual_marker(visual_text)
                self.obs_controller.trigger_audio_marker()

            if self.audio_initialized and self.audio_player.playing:
                self.audio_player.play_sound(full_sequence)
                time.sleep(len(full_sequence) / sample_rate)
            else:
                sd.play(full_sequence, samplerate=sample_rate, blocking=True)

            if self.obs_connected:
                time.sleep(1.0)
                self.obs_controller.hide_visual_marker()

            print(f"Sync markers triggered: {label}")
        except Exception as e:
            print(f"ERROR: Could not trigger sync markers ({label}): {e}")

    def _show_message(self, message):
        popup = tk.Toplevel(self.master)
        popup.title("Message")
        popup.transient(self.master)
        popup.grab_set()
        popup.configure(bg="black")

        label = tk.Label(
            popup,
            text=message,
            font=("Helvetica", 14),
            bg="black",
            fg="white",
            wraplength=400,
        )
        label.pack(padx=20, pady=20)

        button = tk.Button(
            popup,
            text="OK",
            command=popup.destroy,
            font=("Helvetica", 12),
            bg="#007bff",
            fg="white",
        )
        button.pack(pady=(0, 20))

        popup.update_idletasks()
        x = (self.master.winfo_screenwidth() - popup.winfo_width()) // 2
        y = (self.master.winfo_screenheight() - popup.winfo_height()) // 2
        popup.geometry(f"+{x}+{y}")

    def _exit_app(self, event=None):
        if self.shutdown_flag.is_set():
            return

        if not self.recording:
            # Eye tracker failed, subscribe failed, or user exits before stream starts.
            print("Exiting…")
            self.shutdown_flag.set()
            try:
                if self.csv_file:
                    self.csv_file.close()
                    self.csv_file = None
            except Exception as e:
                print(f"Warning closing CSV: {e}")
            try:
                if self.obs_connected:
                    self.obs_controller.disconnect()
            except Exception:
                pass
            self.master.quit()
            return

        print("Stopping recording...")
        self.recording = False

        def end_and_close():
            try:
                self._trigger_sync_markers("END")
                self.shutdown_flag.set()

                if self.audio_initialized:
                    self.audio_player.stop_stream()

                if self.eye_tracker:
                    try:
                        self.eye_tracker.unsubscribe_from(
                            tobii_research.EYETRACKER_GAZE_DATA, self._gaze_data_callback
                        )
                    except Exception:
                        pass

                if self.obs_connected:
                    self.obs_controller.disconnect()

                if self.csv_file:
                    self.csv_file.close()
            except Exception as e:
                print(f"ERROR during shutdown: {e}")
            finally:
                self.master.after(1000, self.master.quit)

        threading.Thread(target=end_and_close, daemon=True).start()

    def _minimize_window(self, event=None):
        self.master.iconify()


if __name__ == "__main__":
    print("=" * 60)
    print("Tobii Gaze Recorder with OBS Sync")
    print("=" * 60)
    print("NOTE: This software requires the Tobii Pro SDK,")
    print("which must be installed separately from Tobii AB.")
    print("  https://www.tobiipro.com/product-listing/tobii-pro-sdk/")
    print("See README.md for full installation instructions.")
    print("=" * 60)
    load_config()
    try:
        root = tk.Tk()
        app = GazeRecorder(root)
        root.mainloop()
    except Exception as e:
        print(f"FATAL ERROR: {e}")