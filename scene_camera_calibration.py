# scene_camera_calibration.py

import cv2
import numpy as np
import pickle
import csv
import bisect
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageTk


class ToolTip:
    """Show a tooltip on hover for any Tkinter widget."""

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip_window = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, event=None):
        if self.tip_window:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(
            tw,
            text=self.text,
            background="#ffffe0",
            relief="solid",
            borderwidth=1,
            font=("Helvetica", 10),
            wraplength=280,
            justify="left",
        ).pack(ipadx=4, ipady=2)

    def _hide(self, event=None):
        if self.tip_window:
            self.tip_window.destroy()
            self.tip_window = None


class SceneCameraCalibrator:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Scene Camera Calibration")

        # Adapt window size to the screen, with sensible maximums
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        win_w = min(screen_w - 100, 1400)
        win_h = min(screen_h - 100, 950)
        self.root.geometry(f"{win_w}x{win_h}")

        # Video and calibration state
        self.video_path = None
        self.video_cap = None
        self.eyetracker_calib_settings = None
        self.scene_calib_data = None
        self.gaze_csv_path = None
        self.gaze_data = []

        # Video frame state
        self.current_frame = None
        self.current_frame_idx = 0
        self.total_frames = 0
        self.fps = 30.0
        self.video_width = 0
        self.video_height = 0

        # Display scaling
        self.displayed_width = 0
        self.displayed_height = 0
        self.display_scale = 1.0

        # Calibration points
        self.calibration_points_screen = []
        self.calibration_points_video = []
        self.current_calib_point_idx = 0
        self.calibrating = False
        self.hover_preview_pos = None

        # Sync
        self.sync_frame_idx = 0
        self.sync_frame_set = False
        
        # Tutorial State
        self.tutorial_active = False

        self.create_ui()

    # -----------------------------
    # UI Layout
    # -----------------------------

    def create_ui(self):
        # Remove default menu bar (if any was set previously)
        self.root.config(menu="")

        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # --- LEFT PANEL: VIDEO ---
        left_panel = ttk.Frame(main_frame)
        left_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.video_label = ttk.Label(left_panel, text="No video loaded", background="black")
        self.video_label.pack(fill=tk.BOTH, expand=True)
        self.video_label.bind("<Button-1>", self.on_video_click)
        self.video_label.bind("<Motion>", self.on_mouse_motion)

        control_frame = ttk.Frame(left_panel)
        control_frame.pack(fill=tk.X, pady=5)

        # Playback Controls
        btn_rr = ttk.Button(control_frame, text="<<", command=lambda: self.seek_frame(-10))
        btn_rr.pack(side=tk.LEFT)
        ToolTip(btn_rr, "Jump back 10 frames")

        btn_r = ttk.Button(control_frame, text="<", command=lambda: self.seek_frame(-1))
        btn_r.pack(side=tk.LEFT)
        ToolTip(btn_r, "Go back 1 frame")

        btn_f = ttk.Button(control_frame, text=">", command=lambda: self.seek_frame(1))
        btn_f.pack(side=tk.LEFT)
        ToolTip(btn_f, "Advance 1 frame")

        btn_ff = ttk.Button(control_frame, text=">>", command=lambda: self.seek_frame(10))
        btn_ff.pack(side=tk.LEFT)
        ToolTip(btn_ff, "Jump forward 10 frames")

        self.frame_label = ttk.Label(control_frame, text="Frame: 0 / 0")
        self.frame_label.pack(side=tk.LEFT, padx=15)

        # Sync Button (Direct access)
        self.btn_set_sync = ttk.Button(control_frame, text="Set Sync Frame (Current)", command=self.set_sync_frame_current)
        self.btn_set_sync.pack(side=tk.LEFT, padx=10)
        ToolTip(self.btn_set_sync, "Mark the current video frame as the synchronization point — use the frame where you heard the START audio tone")

        # Slider for scrubbing
        self.slider_var = tk.DoubleVar()
        self.time_slider = ttk.Scale(
            control_frame, 
            from_=0, 
            to=1, 
            orient=tk.HORIZONTAL, 
            variable=self.slider_var,
            command=self.on_slider_drag
        )
        self.time_slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10)

        # --- RIGHT PANEL: CONTROLS ---
        right_panel = ttk.Frame(main_frame, width=350)
        right_panel.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        right_panel.pack_propagate(False) # Force width

        # 1. Status Area
        status_frame = ttk.LabelFrame(right_panel, text="Status", padding=10)
        status_frame.pack(fill=tk.X, pady=(0, 10))
        self.status_text = tk.Text(status_frame, height=6, wrap=tk.WORD, font=("Arial", 9))
        self.status_text.pack(fill=tk.BOTH, expand=True)

        # 2. Instructions
        instr_frame = ttk.LabelFrame(right_panel, text="Instructions", padding=10)
        instr_frame.pack(fill=tk.X, pady=(0, 10))
        self.instructions_text = tk.Label(
            instr_frame, 
            text="Welcome.\nSelect a workflow below to begin.",
            wraplength=310, 
            justify="left",
            anchor="w"
        )
        self.instructions_text.pack(fill=tk.X)
        
        # Tutorial Button
        btn_tutorial = ttk.Button(instr_frame, text="First Time User Tutorial", command=self.run_tutorial)
        btn_tutorial.pack(fill=tk.X, pady=(5, 0))
        ToolTip(btn_tutorial, "Walk through the complete workflow step by step with guided instructions — recommended for first-time users")

        # 3. Primary Workflows (Top Right)
        workflow_frame = ttk.LabelFrame(right_panel, text="Workflows", padding=10)
        workflow_frame.pack(fill=tk.X, pady=(0, 10))

        btn_calib = ttk.Button(workflow_frame, text="1. Calibrate Scene Camera", command=self.workflow_calibrate)
        btn_calib.pack(fill=tk.X, pady=2)
        ToolTip(btn_calib, "Step 1: Load your calibration video and eye-tracker calibration file, then click each calibration point on the video to align it with the screen")

        btn_sync = ttk.Button(workflow_frame, text="2. Synchronize & Process", command=self.workflow_synchronize)
        btn_sync.pack(fill=tk.X, pady=2)
        ToolTip(btn_sync, "Step 2: Load your experiment video and gaze data, then align the gaze timeline with the video using the sync marker")

        # 4. Processing Outputs
        output_frame = ttk.LabelFrame(right_panel, text="Outputs", padding=10)
        output_frame.pack(fill=tk.X, pady=(0, 10))

        btn_video = ttk.Button(output_frame, text="Generate Gaze Video", command=self.generate_gaze_video)
        btn_video.pack(fill=tk.X, pady=2)
        ToolTip(btn_video, "Render an MP4 video with a red gaze cursor overlaid on the scene camera footage")

        btn_csv = ttk.Button(output_frame, text="Export Converted CSV", command=self.export_converted_table)
        btn_csv.pack(fill=tk.X, pady=2)
        ToolTip(btn_csv, "Save a CSV file with gaze coordinates converted into scene camera pixel space (adds scene_left_x/y and scene_right_x/y columns)")

        # 5. Manual Loaders (Bottom Right)
        manual_frame = ttk.LabelFrame(right_panel, text="Manual Load", padding=10)
        manual_frame.pack(fill=tk.X, pady=(0, 10))
        
        btn_lv = ttk.Button(manual_frame, text="Load Video", command=self.load_video)
        btn_lv.pack(fill=tk.X, pady=1)
        ToolTip(btn_lv, "Load a scene camera video file (.mp4, .avi, .mov, .mkv)")

        btn_lec = ttk.Button(manual_frame, text="Load Eye Calib (.pkl)", command=self.load_eyetracker_calib)
        btn_lec.pack(fill=tk.X, pady=1)
        ToolTip(btn_lec, "Load the tobii_calibration.pkl file saved by calibrate_eyetracker.py")

        btn_lsc = ttk.Button(manual_frame, text="Load Scene Calib (.pkl)", command=self.load_scene_calib)
        btn_lsc.pack(fill=tk.X, pady=1)
        ToolTip(btn_lsc, "Load a previously saved scene camera calibration file (.pkl) created by this tool")

        btn_lgc = ttk.Button(manual_frame, text="Load Gaze CSV", command=self.load_gaze_csv)
        btn_lgc.pack(fill=tk.X, pady=1)
        ToolTip(btn_lgc, "Load a gaze recording CSV file saved by record_gaze_OBS.py")

        # 6. Progress
        progress_frame = ttk.LabelFrame(right_panel, text="Progress", padding=10)
        progress_frame.pack(fill=tk.X, side=tk.BOTTOM, pady=10)
        self.progress_bar = ttk.Progressbar(progress_frame, mode='determinate')
        self.progress_bar.pack(fill=tk.X)

        self.log_status("System Ready")
        self.update_instructions("ready")

    # -----------------------------
    # Logic & Workflows
    # -----------------------------

    def log_status(self, message):
        self.status_text.insert(tk.END, f"- {message}\n")
        self.status_text.see(tk.END)
        self.root.update_idletasks()

    def update_instructions(self, state):
        text = ""
        if state == "ready":
            text = (
                "If this is your first time, click 'First Time User Tutorial' for a guided walkthrough.\n\n"
                "Otherwise: use 'Calibrate Scene Camera' to set up a new scene, or 'Synchronize & Process' if you already have a scene calibration file saved."
            )
        elif state == "calibration_ready":
            text = "Files loaded. Use the video controls or slider to find a frame where calibration targets are visible, then click each one in order."
        elif state == "calibrating":
            text = f"Click point {self.current_calib_point_idx + 1} of {len(self.calibration_points_screen)} on the video.\n\nFind the frame where this point is clearly visible and click its center."
        elif state == "sync_needed":
            text = "Use the slider or arrow buttons to find the exact video frame where you heard the START audio tone. Then click 'Set Sync Frame (Current)'."
        elif state == "processing_ready":
            text = "Sync frame is set. You can now:\n- 'Generate Gaze Video' to create a video with gaze overlay\n- 'Export Converted CSV' to save a data file with scene coordinates"
        
        self.instructions_text.config(text=text)

    def run_tutorial(self):
        self.tutorial_active = True
        ans = messagebox.askyesno("Tutorial", 
            "Welcome to the First Time User Tutorial!\n\n"
            "This guide will walk you through the ENTIRE process:\n"
            "1. Calibrating the Scene Camera\n"
            "2. Synchronizing with Gaze Data\n\n"
            "Do you want to start the full tutorial?"
        )
        if ans:
            self.workflow_calibrate()
        else:
            self.tutorial_active = False

    def workflow_calibrate(self):
        """Wizard for Calibration"""
        self.log_status("--- Starting Calibration Workflow ---")
        
        # 1. Check/Load Video
        if not self.video_cap:
            if self.tutorial_active: messagebox.showinfo("Tutorial", "Step 1: Please select the CALIBRATION video file.")
            self.load_video()
            if not self.video_cap: return

        # 2. Check/Load Eye Calib
        if not self.eyetracker_calib_settings:
            if self.tutorial_active: messagebox.showinfo("Tutorial", "Step 2: Load the 'tobii_calibration.pkl' file generated during eye tracker setup.")
            self.load_eyetracker_calib()
            if not self.eyetracker_calib_settings: return

        # 3. Start
        if self.tutorial_active:
            messagebox.showinfo("Tutorial: Calibration Steps", 
                "Step 3: Calibration Mode.\n\n"
                "1. Use the video controls (<< < > >>) or slider to find ANY frame where the calibration points are visible.\n"
                "2. Look for the requested point (e.g., Point 1).\n"
                "3. CLICK the center of that point in the video.\n"
                "4. Repeat for all points in order.\n\n"
                "IMPORTANT: You will save this calibration at the end. You can reuse that saved file for any future videos as long as the 'scene camera' does not change position."
            )
        self.start_calibration()

    def workflow_synchronize(self):
        """Wizard for Synchronization"""
        self.log_status("--- Starting Sync Workflow ---")

        # 1. Check/Load Video (Force load if tutorial active because current video is calibration video)
        if self.tutorial_active or not self.video_cap:
            if self.tutorial_active: messagebox.showinfo("Tutorial", "Step 4: Now, please load the EXPERIMENT video.")
            self.load_video()
            if not self.video_cap: return

        # 2. Check/Load Eye Calib (Requested by prompt)
        if not self.eyetracker_calib_settings:
            if self.tutorial_active: messagebox.showinfo("Tutorial", "Step 5: Load the original 'tobii_calibration.pkl' file.")
            self.load_eyetracker_calib()
            if not self.eyetracker_calib_settings: return

        # 3. Check/Load Scene Calib
        if not self.scene_calib_data:
            if self.tutorial_active: messagebox.showinfo("Tutorial", "Step 6: Load the Scene Camera Calibration (.pkl) you saved earlier.")
            self.load_scene_calib()
            if not self.scene_calib_data: return

        # 4. Check/Load CSV
        if not self.gaze_data:
            if self.tutorial_active: messagebox.showinfo("Tutorial", "Step 7: Load the Gaze Data CSV file.")
            self.load_gaze_csv()
            if not self.gaze_data: return

        # 5. Instructions for Sync
        if self.tutorial_active:
             messagebox.showinfo("Tutorial", 
                "Step 8: Synchronization.\n\n"
                "Use the slider below the video to find the exact frame where the 'START' marker/sound occurs.\n"
                "When you find it, click the 'Set Sync Frame (Current)' button."
            )
        self.log_status("Please find the sync frame and press 'Set Sync Frame'.")
        self.update_instructions("sync_needed")

    # -----------------------------
    # Mouse & Video Handling
    # -----------------------------

    def on_slider_drag(self, val):
        if self.video_cap is None: return
        target_frame = int(float(val))
        if target_frame != self.current_frame_idx:
            self.show_frame(target_frame)

    def on_mouse_motion(self, event):
        if not self.calibrating or self.current_frame is None:
            if self.hover_preview_pos is not None:
                self.hover_preview_pos = None
                self.show_frame(self.current_frame_idx)
            return

        if not hasattr(self, 'displayed_width') or not hasattr(self, 'display_scale'):
            return

        # Calculate coordinates relative to video
        label_width = self.video_label.winfo_width()
        label_height = self.video_label.winfo_height()
        offset_x = max(0, (label_width - self.displayed_width) // 2)
        offset_y = max(0, (label_height - self.displayed_height) // 2)

        mouse_x = event.x - offset_x
        mouse_y = event.y - offset_y

        if mouse_x < 0 or mouse_x >= self.displayed_width or mouse_y < 0 or mouse_y >= self.displayed_height:
            if self.hover_preview_pos is not None:
                self.hover_preview_pos = None
                self.show_frame(self.current_frame_idx)
            return

        video_x = int(mouse_x / self.display_scale)
        video_y = int(mouse_y / self.display_scale)
        video_x = max(0, min(video_x, self.video_width - 1))
        video_y = max(0, min(video_y, self.video_height - 1))

        self.hover_preview_pos = (video_x, video_y)
        self.show_frame(self.current_frame_idx)

    def show_frame(self, frame_idx):
        if self.video_cap is None: return

        frame_idx = max(0, min(frame_idx, self.total_frames - 1))
        self.current_frame_idx = frame_idx
        self.slider_var.set(frame_idx)

        self.video_cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = self.video_cap.read()
        if not ret: return

        self.current_frame = frame.copy()

        # Draw existing calibration points
        if self.calibrating:
            for i, pt in enumerate(self.calibration_points_video):
                cv2.circle(frame, pt, 10, (0, 255, 0), 2)
                cv2.putText(frame, str(i + 1), (pt[0] + 15, pt[1]), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

            # Draw hover preview
            if self.hover_preview_pos is not None:
                hx, hy = self.hover_preview_pos
                cv2.circle(frame, (hx, hy), 12, (0, 255, 255), 2)
                cv2.circle(frame, (hx, hy), 3, (0, 255, 255), -1)

        # Draw sync marker if set
        if self.sync_frame_set and abs(self.sync_frame_idx - frame_idx) < 5:
             cv2.putText(frame, "SYNC FRAME", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0,0,255), 3)

        # Display Logic
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        # Calculate scale
        max_w = 900
        max_h = 700
        scale_w = max_w / frame.shape[1]
        scale_h = max_h / frame.shape[0]
        scale = min(scale_w, scale_h)

        display_width = int(frame.shape[1] * scale)
        display_height = int(frame.shape[0] * scale)

        self.displayed_width = display_width
        self.displayed_height = display_height
        self.display_scale = scale

        frame_resized = cv2.resize(frame_rgb, (display_width, display_height))
        img = Image.fromarray(frame_resized)
        photo = ImageTk.PhotoImage(image=img)

        self.video_label.config(image=photo, text="")
        self.video_label.image = photo
        self.frame_label.config(text=f"Frame: {frame_idx} / {self.total_frames}")

    def on_video_click(self, event):
        if not self.calibrating or self.current_frame is None:
            return

        if self.hover_preview_pos:
            video_x, video_y = self.hover_preview_pos
        else:
            # Fallback calculation if mouse hasn't moved but clicked
            if not hasattr(self, 'displayed_width'): return
            label_width = self.video_label.winfo_width()
            label_height = self.video_label.winfo_height()
            offset_x = max(0, (label_width - self.displayed_width) // 2)
            offset_y = max(0, (label_height - self.displayed_height) // 2)
            click_x = event.x - offset_x
            click_y = event.y - offset_y
            if click_x < 0 or click_x >= self.displayed_width: return
            video_x = int(click_x / self.display_scale)
            video_y = int(click_y / self.display_scale)

        self.calibration_points_video.append((video_x, video_y))
        self.log_status(f"Recorded Point {len(self.calibration_points_video)} at ({video_x}, {video_y})")
        
        self.current_calib_point_idx += 1
        self.show_next_calibration_point()
        self.show_frame(self.current_frame_idx)

    # -----------------------------
    # File Operations
    # -----------------------------

    def load_video(self):
        filepath = filedialog.askopenfilename(title="Select Scene Camera Video", filetypes=[("Video", "*.mp4 *.avi *.mov *.mkv")])
        if not filepath: return

        self.video_path = filepath
        self.video_cap = cv2.VideoCapture(filepath)
        if not self.video_cap.isOpened():
            messagebox.showerror("Error", "Could not open video.")
            return

        self.total_frames = int(self.video_cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.fps = self.video_cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.video_width = int(self.video_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.video_height = int(self.video_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
        self.time_slider.config(to=self.total_frames - 1)
        self.log_status(f"Video Loaded: {os.path.basename(filepath)}")
        self.show_frame(0)

    def load_eyetracker_calib(self):
        filepath = filedialog.askopenfilename(title="Select Eye Calibration (.pkl)", filetypes=[("Pickle", "*.pkl")])
        if not filepath: return
        try:
            with open(filepath, "rb") as f:
                self.eyetracker_calib_settings = pickle.load(f)
            self.calibration_points_screen = self.eyetracker_calib_settings.get("calibration_points_normalized", [])
            self.log_status("Eye Calib Loaded.")
        except Exception as e:
            messagebox.showerror("Error", f"Load failed: {e}")

    def load_scene_calib(self):
        filepath = filedialog.askopenfilename(title="Select Scene Calibration (.pkl)", filetypes=[("Pickle", "*.pkl")])
        if not filepath: return
        try:
            with open(filepath, "rb") as f:
                self.scene_calib_data = pickle.load(f)
            self.log_status("Scene Calib Loaded.")
        except Exception as e:
            messagebox.showerror("Error", f"Load failed: {e}")

    def load_gaze_csv(self):
        filepath = filedialog.askopenfilename(title="Select Gaze CSV", filetypes=[("CSV", "*.csv")])
        if not filepath: return
        self.gaze_csv_path = filepath
        try:
            with open(filepath, "r", newline="") as f:
                self.gaze_data = list(csv.DictReader(f))
            self.log_status(f"CSV Loaded ({len(self.gaze_data)} rows).")
        except Exception as e:
            messagebox.showerror("Error", f"Load failed: {e}")

    # -----------------------------
    # Functionality
    # -----------------------------

    def seek_frame(self, delta):
        if self.video_cap: self.show_frame(self.current_frame_idx + delta)

    def set_sync_frame_current(self):
        if self.video_cap is None: return
        self.sync_frame_idx = self.current_frame_idx
        self.sync_frame_set = True
        self.log_status(f"Sync Frame SET to: {self.sync_frame_idx}")
        self.update_instructions("processing_ready")
        messagebox.showinfo("Sync Set", f"Synchronization frame set to {self.sync_frame_idx}.\nYou can now Process or Export.")

    def start_calibration(self):
        self.calibrating = True
        self.calibration_points_video = []
        self.current_calib_point_idx = 0
        self.update_instructions("calibrating")
        self.show_next_calibration_point()

    def show_next_calibration_point(self):
        if self.current_calib_point_idx >= len(self.calibration_points_screen):
            self.finish_calibration()
            return

        pt = self.calibration_points_screen[self.current_calib_point_idx]
        sw = self.eyetracker_calib_settings["screen_width_px"]
        sh = self.eyetracker_calib_settings["screen_height_px"]
        px = int(pt[0] * sw)
        py = int(pt[1] * sh)

        msg = f"Action: Click point {self.current_calib_point_idx + 1} (Tobii coords: {px}, {py})"
        self.log_status(msg)
        self.update_instructions("calibrating")

    def finish_calibration(self):
        self.calibrating = False
        sw = self.eyetracker_calib_settings["screen_width_px"]
        sh = self.eyetracker_calib_settings["screen_height_px"]

        screen_pts = np.float32([[p[0]*sw, p[1]*sh] for p in self.calibration_points_screen])
        video_pts = np.float32(self.calibration_points_video)

        if len(video_pts) < 4:
            messagebox.showerror("Error", "Need at least 4 points.")
            return

        H, _ = cv2.findHomography(screen_pts, video_pts, cv2.RANSAC, 5.0)
        if H is None:
            messagebox.showerror("Error", "Homography failed.")
            return

        self.scene_calib_data = {
            "homography_matrix": H.tolist(),
            "screen_resolution": (sw, sh),
            "video_resolution": (self.video_width, self.video_height)
        }

        output_path = filedialog.asksaveasfilename(defaultextension=".pkl", filetypes=[("Pickle", "*.pkl")])
        if output_path:
            with open(output_path, "wb") as f: pickle.dump(self.scene_calib_data, f)
            self.log_status("Calibration Saved.")
            
            # Transition to Synchronization if in Tutorial Mode
            if self.tutorial_active:
                messagebox.showinfo("Tutorial",
                    "Calibration Complete & Saved!\n\n"
                    "We will now proceed to Synchronization.\n"
                    "You will now need to load your EXPERIMENT video and Gaze Data."
                )
                self.workflow_synchronize()
            else:
                self.update_instructions("ready")

    def export_converted_table(self):
        if not self.scene_calib_data or not self.gaze_data:
            messagebox.showwarning("Missing Data", "Need Scene Calibration and Gaze CSV.")
            return

        default_name = "converted_gaze_scene_camera.csv"
        if self.gaze_csv_path:
            default_name = os.path.splitext(os.path.basename(self.gaze_csv_path))[0] + "_scene_camera.csv"

        out_path = filedialog.asksaveasfilename(defaultextension=".csv", initialfile=default_name)
        if not out_path: return

        try:
            H = np.array(self.scene_calib_data["homography_matrix"])
            sw, sh = self.scene_calib_data["screen_resolution"]
            # Fallback for video res if video not loaded
            vw, vh = self.video_width, self.video_height
            if vw == 0: vw, vh = self.scene_calib_data["video_resolution"]

            new_fields = ["scene_left_x", "scene_left_y", "scene_right_x", "scene_right_y"]
            fieldnames = list(self.gaze_data[0].keys()) + new_fields
            
            converted = []
            total = len(self.gaze_data)
            self.progress_bar["value"] = 0

            for i, row in enumerate(self.gaze_data):
                new_row = row.copy()
                pairs = [("left_x", "left_y", "scene_left_x", "scene_left_y"),
                         ("right_x", "right_y", "scene_right_x", "scene_right_y")]
                
                for ox, oy, sx, sy in pairs:
                    try:
                        vx, vy = float(row.get(ox)), float(row.get(oy))
                        # Screen Norm -> Screen Pixels -> Transform -> Video Pixels -> Video Norm
                        px = vx * sw
                        py = vy * sh

                        pt = np.array([[[px, py]]], dtype=np.float32)
                        res = cv2.perspectiveTransform(pt, H)

                        v_px = res[0, 0, 0]
                        v_py = res[0, 0, 1]

                        new_row[sx] = f"{v_px / vw:.6f}"
                        new_row[sy] = f"{v_py / vh:.6f}"
                    except (ValueError, TypeError):
                        new_row[sx] = ""
                        new_row[sy] = ""
                
                converted.append(new_row)
                if i % 500 == 0: 
                    self.progress_bar["value"] = (i/total)*100
                    self.root.update_idletasks()
            
            with open(out_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(converted)
            
            self.progress_bar["value"] = 100
            messagebox.showinfo("Success", f"Saved to {os.path.basename(out_path)}")
        except Exception as e:
            messagebox.showerror("Error", f"Export failed: {e}")
            self.progress_bar["value"] = 0

    def generate_gaze_video(self):
        if not self.video_cap or not self.scene_calib_data or not self.gaze_data:
            messagebox.showwarning("Missing Data", "Need Video, Calibration, and CSV.")
            return

        if not self.sync_frame_set:
            if not messagebox.askyesno(
                "Sync Frame Not Set",
                "You have not set a sync frame.\n"
                "Export will start from frame 0.\n\n"
                "Continue anyway?",
            ):
                return

        out_path = filedialog.asksaveasfilename(defaultextension=".mp4", filetypes=[("MP4","*.mp4")])
        if not out_path: return

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        out = cv2.VideoWriter(out_path, fourcc, self.fps, (self.video_width, self.video_height))

        H = np.array(self.scene_calib_data["homography_matrix"])
        sw, sh = self.scene_calib_data["screen_resolution"]

        # 1. Find Sync Time
        start_ts = None
        time_col = "timestamp_ms" if "timestamp_ms" in self.gaze_data[0] else "timestamp_system"
        sync_col = None
        if "sync_marker" in self.gaze_data[0]:
            sync_col = "sync_marker"
        elif "sync_event" in self.gaze_data[0]:
            sync_col = "sync_event"

        if sync_col is not None:
            for r in self.gaze_data:
                if "START" in str(r.get(sync_col, "")):
                    start_ts = float(r[time_col])
                    break
        
        if start_ts is None:
            if not messagebox.askyesno("Sync Warning", "No 'START' marker found. Use first row as start?"):
                out.release()
                return
            start_ts = float(self.gaze_data[0][time_col])

        # 2. Build Lookup
        lookup = []
        for r in self.gaze_data:
            try:
                t = float(r[time_col])
                t_rel = (t - start_ts) * 1000.0 if time_col == "timestamp_system" else (t - start_ts)
                lookup.append((t_rel, r))
            except (ValueError, TypeError):
                pass
        
        lookup.sort(key=lambda x: x[0])
        times = [x[0] for x in lookup]
        rows = [x[1] for x in lookup]
        n = len(times)

        # 3. Render
        self.video_cap.set(cv2.CAP_PROP_POS_FRAMES, self.sync_frame_idx)
        frames_to_do = max(0, self.total_frames - self.sync_frame_idx)
        processed = 0

        while True:
            ret, frame = self.video_cap.read()
            if not ret: break

            vid_ms = (processed / self.fps) * 1000.0
            idx = bisect.bisect_left(times, vid_ms)
            
            # Find closest gaze sample
            best_r = None
            min_d = float('inf')
            for i in [idx-1, idx]:
                if 0 <= i < n:
                    d = abs(times[i] - vid_ms)
                    if d < min_d:
                        min_d = d
                        best_r = rows[i]
            
            if best_r:
                try:
                    # Logic for averaging eyes
                    lx, ly = float(best_r.get("left_x", "nan")), float(best_r.get("left_y", "nan"))
                    rx, ry = float(best_r.get("right_x", "nan")), float(best_r.get("right_y", "nan"))
                    
                    gx, gy = None, None
                    if not np.isnan(lx): gx, gy = lx, ly
                    if not np.isnan(rx): gx, gy = rx, ry
                    if not np.isnan(lx) and not np.isnan(rx): gx, gy = (lx+rx)/2, (ly+ry)/2

                    if gx is not None:
                        pt = np.array([[[gx * sw, gy * sh]]], dtype=np.float32)
                        res = cv2.perspectiveTransform(pt, H)
                        vx, vy = int(res[0, 0, 0]), int(res[0, 0, 1])
                        cv2.circle(frame, (vx, vy), 20, (0, 0, 255), 3)
                except (ValueError, TypeError):
                    pass

            out.write(frame)
            processed += 1
            if processed % 30 == 0 and frames_to_do > 0:
                self.progress_bar["value"] = (processed / frames_to_do) * 100
                self.root.update_idletasks()

        out.release()
        self.progress_bar["value"] = 100
        messagebox.showinfo("Done", "Video Generation Complete.")

if __name__ == "__main__":
    print("=" * 60)
    print("Scene Camera Calibration Tool")
    print("=" * 60)
    print("NOTE: This software interfaces with the Tobii Pro SDK,")
    print("which must be installed separately from Tobii AB.")
    print("  https://www.tobiipro.com/product-listing/tobii-pro-sdk/")
    print("See README.md for full installation instructions.")
    print("=" * 60)
    SceneCameraCalibrator().root.mainloop()