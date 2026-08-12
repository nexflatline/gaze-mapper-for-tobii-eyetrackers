# calibrate_eyetracker.py

import tkinter as tk
from tkinter import filedialog
import os
import time
import threading
import sys
import pickle
import math

# --- Configuration ---
FIXATION_DURATION = 500  # Time in ms to stare at point before recording

# --- Try to import Pillow for robust GIF support ---
try:
    from PIL import Image, ImageTk
    has_pillow = True
except ImportError:
    has_pillow = False
    print("Pillow not found. Animated GIFs will not work properly.")

# --- Tobii Research API ---
try:
    import tobii_research
except ImportError:
    print("ERROR: Tobii Research SDK not found.")
    print("Please download and install the Tobii Pro SDK from:")
    print("  https://www.tobiipro.com/product-listing/tobii-pro-sdk/")
    print("See README.md for full installation instructions.")
    sys.exit(1)


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


class CalibrationApp:
    def __init__(self, master):
        self.master = master
        master.title("Tobii Eye Tracker Calibration")

        self.width = master.winfo_screenwidth()
        self.height = master.winfo_screenheight()

        self.eye_tracker = None
        self.calibration_object = None
        self.current_point_index = 0
        self.calibration_data = None
        self.gaze_data_subscription = None
        self.is_exiting = False
        self.is_calibration_active = False
        self.recalibrating_single_point = False  # Flag for single point repair

        # GIF animation variables
        self.calibration_image = None
        self.gif_frames = []
        self.gif_delays = []
        self.gif_frame_index = 0
        self.gif_animation_job = None
        self.has_pillow = has_pillow

        # Headbox guide variables
        self.headbox_canvas = None
        self.head_indicator = None

        # Calibration point options
        self.point_choice = tk.StringVar(value="9")
        self.selected_image_path = tk.StringVar(value="No image selected.")

        # 5- and 9-point patterns in normalized screen coordinates
        self.five_points = [
            (0.1, 0.1),
            (0.9, 0.1),
            (0.1, 0.9),
            (0.9, 0.9),
            (0.5, 0.5),
        ]
        self.nine_points = [
            (0.1, 0.1),
            (0.5, 0.1),
            (0.9, 0.1),
            (0.1, 0.5),
            (0.5, 0.5),
            (0.9, 0.5),
            (0.1, 0.9),
            (0.5, 0.9),
            (0.9, 0.9),
        ]
        self.calibration_points = self.nine_points

        self._message_box = None

        # Setup initial window
        self.master.geometry(
            f"800x600+{int(self.width/2 - 400)}+{int(self.height/2 - 300)}"
        )
        self._setup_options_screen()

        # Initialize eye tracker
        self._initialize_eye_tracker()

        # Protocol for window close
        self.master.protocol("WM_DELETE_WINDOW", self._exit_app)

    # ---------------------------------
    # Eye tracker and headbox
    # ---------------------------------
    def _initialize_eye_tracker(self):
        print("Searching for eye trackers...")
        self.master.update_idletasks()
        found = tobii_research.find_all_eyetrackers()
        if not found:
            self._show_message(
                "No eye trackers found. Connect your device.", lambda: self._exit_app()
            )
            return

        self.eye_tracker = found[0]
        print(f"Found eye tracker: {self.eye_tracker.serial_number}")

        # Start gaze data stream for headbox guide
        try:
            self.gaze_data_subscription = self.eye_tracker.subscribe_to(
                tobii_research.EYETRACKER_GAZE_DATA, self._update_headbox_guide
            )
            print("Successfully subscribed to gaze data")
        except Exception as e:
            print(f"Error subscribing to gaze data: {e}")

    def _update_headbox_guide(self, gaze_data):
        if self.is_exiting:
            return
        try:
            self.master.after(1, self._process_gaze_for_headbox, gaze_data)
        except tk.TclError:
            pass

    def _process_gaze_for_headbox(self, gaze_data):
        if self.is_exiting or not self.headbox_canvas or not self.head_indicator:
            return

        gaze_origin_obj = None
        if hasattr(gaze_data, "left_eye") and gaze_data.left_eye is not None:
            left_eye = gaze_data.left_eye
            if hasattr(left_eye, "gaze_origin") and left_eye.gaze_origin is not None:
                gaze_origin_obj = left_eye.gaze_origin

        if (
            gaze_origin_obj is None
            and hasattr(gaze_data, "right_eye")
            and gaze_data.right_eye is not None
        ):
            right_eye = gaze_data.right_eye
            if hasattr(right_eye, "gaze_origin") and right_eye.gaze_origin is not None:
                gaze_origin_obj = right_eye.gaze_origin

        if gaze_origin_obj is None:
            try:
                self.headbox_canvas.itemconfigure(self.head_indicator, fill="red")
            except tk.TclError:
                pass
            return

        x = y = z = None
        coords_type = None

        if hasattr(gaze_origin_obj, "position_in_trackbox_coordinates"):
            coords = gaze_origin_obj.position_in_trackbox_coordinates
            if coords is not None and len(coords) >= 3:
                x, y, z = coords[0], coords[1], coords[2]
                coords_type = "trackbox"
        elif hasattr(gaze_origin_obj, "position_in_user_coordinates"):
            coords = gaze_origin_obj.position_in_user_coordinates
            if coords is not None and len(coords) >= 3:
                x, y, z = coords[0], coords[1], coords[2]
                coords_type = "user"

        if x is None or y is None or z is None:
            try:
                self.headbox_canvas.itemconfigure(self.head_indicator, fill="red")
            except tk.TclError:
                pass
            return

        if (isinstance(x, float) and x != x): # NaN check
            try:
                self.headbox_canvas.itemconfigure(self.head_indicator, fill="red")
            except tk.TclError:
                pass
            return

        if coords_type == "trackbox":
            canvas_x = 50 + (x * 200)
            canvas_y = 180 - (y * 160)
            size_factor = 1.2 - z
        elif coords_type == "user":
            norm_x = (x + 150) / 300
            norm_y = (y + 100) / 200
            norm_z = (z - 500) / 300
            canvas_x = 50 + (norm_x * 200)
            canvas_y = 180 - (norm_y * 160)
            size_factor = 1.2 - norm_z
        else:
            return

        size_factor = max(0.5, min(1.5, size_factor))
        base_size = 20
        head_size = base_size * size_factor

        if 120 < canvas_x < 180 and 80 < canvas_y < 120:
            color = "#28a745"
        elif 80 < canvas_x < 220 and 50 < canvas_y < 150:
            color = "#ffc107"
        else:
            color = "#dc3545"

        try:
            self.headbox_canvas.coords(
                self.head_indicator,
                canvas_x - head_size / 2,
                canvas_y - head_size / 2,
                canvas_x + head_size / 2,
                canvas_y + head_size / 2,
            )
            self.headbox_canvas.itemconfigure(self.head_indicator, fill=color)
        except tk.TclError:
            pass

    # ---------------------------------
    # Options screen
    # ---------------------------------
    def _setup_options_screen(self):
        self.setup_frame = tk.Frame(self.master, bg="black")
        self.setup_frame.pack(fill=tk.BOTH, expand=True)

        main_container = tk.Frame(self.setup_frame, bg="black")
        main_container.place(relx=0.5, rely=0.5, anchor="center")

        # Left: calibration options
        options_frame = tk.Frame(main_container, bg="black")
        options_frame.pack(side=tk.LEFT, padx=50, pady=20)

        tk.Label(
            options_frame,
            text="Tobii Eye Tracker Calibration Setup",
            font=("Helvetica", 24),
            fg="white",
            bg="black",
        ).pack(pady=20)

        point_frame = tk.Frame(options_frame, bg="black")
        point_frame.pack(pady=10)

        tk.Label(
            point_frame,
            text="Choose number of calibration points:",
            font=("Helvetica", 16),
            fg="white",
            bg="black",
        ).pack(side=tk.LEFT, padx=10)

        ToolTip(
            tk.Radiobutton(
                point_frame,
                text="5 Points",
                variable=self.point_choice,
                value="5",
                font=("Helvetica", 14),
                fg="white",
                bg="black",
                selectcolor="#222",
            ),
            "Use 5 calibration points — faster, slightly less accurate",
        ).widget.pack(side=tk.LEFT)

        ToolTip(
            tk.Radiobutton(
                point_frame,
                text="9 Points",
                variable=self.point_choice,
                value="9",
                font=("Helvetica", 14),
                fg="white",
                bg="black",
                selectcolor="#222",
            ),
            "Use 9 calibration points — slower, more accurate (recommended)",
        ).widget.pack(side=tk.LEFT)

        image_frame = tk.Frame(options_frame, bg="black")
        image_frame.pack(pady=10)

        tk.Label(
            image_frame,
            text="Calibration target image (optional):",
            font=("Helvetica", 16),
            fg="white",
            bg="black",
        ).pack(side=tk.LEFT, padx=10)

        gif_btn = tk.Button(
            image_frame,
            text="Browse for GIF",
            command=self._browse_for_image,
            font=("Helvetica", 12),
            bg="#007bff",
            fg="white",
        )
        gif_btn.pack(side=tk.LEFT)
        ToolTip(gif_btn, "Select an animated GIF to use as the fixation target during calibration. Leave empty to use a plain white dot.")

        tk.Label(
            options_frame,
            textvariable=self.selected_image_path,
            font=("Helvetica", 10),
            fg="white",
            bg="black",
        ).pack(pady=5)

        instructions_frame = tk.Frame(options_frame, bg="black")
        instructions_frame.pack(pady=20, fill="x")

        tk.Label(
            instructions_frame,
            text="Calibration Instructions:",
            font=("Helvetica", 18, "bold"),
            fg="#ffc107",
            bg="black",
        ).pack(pady=(0, 10))

        instructions_text = (
            "1. Position your head in the green zone using the guide on the right\n"
            "2. During calibration, look directly at each target point\n"
            "3. Press ENTER when looking at a target to record data\n"
            "4. Use LEFT/RIGHT arrow keys to navigate between points\n"
            "5. Press ESC when finished to complete calibration\n"
            "6. Keep your head still during data collection"
        )
        tk.Label(
            instructions_frame,
            text=instructions_text,
            font=("Helvetica", 14),
            fg="white",
            bg="black",
            justify="left",
            anchor="w",
        ).pack(pady=5)

        start_btn = tk.Button(
            options_frame,
            text="Start Calibration",
            command=self._prepare_and_start_calibration,
            font=("Helvetica", 16),
            bg="#28a745",
            fg="white",
        )
        start_btn.pack(pady=20)
        ToolTip(start_btn, "Begin the calibration sequence. Make sure the indicator circle on the right is green before starting.")

        exit_btn = tk.Button(
            options_frame,
            text="Exit",
            command=self._exit_app,
            font=("Helvetica", 12),
            bg="#dc3545",
            fg="white",
        )
        exit_btn.pack(pady=10)
        ToolTip(exit_btn, "Close the calibration application")

        # Right: headbox guide
        guide_frame = tk.Frame(main_container, bg="black")
        guide_frame.pack(side=tk.RIGHT, padx=50)

        tk.Label(
            guide_frame,
            text="Real-Time Headbox Guide",
            font=("Helvetica", 18),
            fg="white",
            bg="black",
        ).pack(pady=10)

        self.headbox_canvas = tk.Canvas(
            guide_frame, width=300, height=200, bg="#333", highlightthickness=0
        )
        self.headbox_canvas.pack()

        self.headbox_canvas.create_rectangle(
            50, 20, 250, 180, outline="#ffc107", width=2
        )
        self.headbox_canvas.create_text(
            150, 10, text="Acceptable Range", fill="#ffc107", font=("Helvetica", 10)
        )
        self.headbox_canvas.create_rectangle(
            120, 80, 180, 120, outline="#28a745", width=2
        )
        self.headbox_canvas.create_text(
            150, 70, text="Optimal Range", fill="#28a745", font=("Helvetica", 10)
        )

        self.head_indicator = self.headbox_canvas.create_oval(
            140, 90, 160, 110, fill="#dc3545", outline="white"
        )

        tk.Label(
            guide_frame,
            text="Move your head to make the circle green.",
            font=("Helvetica", 12),
            fg="white",
            bg="black",
        ).pack(pady=5)

        tk.Label(
            guide_frame,
            text="Press 'Start Calibration' when ready.",
            font=("Helvetica", 12),
            fg="white",
            bg="black",
        ).pack(pady=5)

        self.master.bind("<Escape>", self._exit_app)

    # ---------------------------------
    # Image / GIF handling
    # ---------------------------------
    def _browse_for_image(self):
        file_path = filedialog.askopenfilename(
            title="Select a GIF image (ideal size ~30x30px)",
            filetypes=[("GIF files", "*.gif")],
        )
        if file_path:
            self.selected_image_path.set(
                f"Image selected: {os.path.basename(file_path)}"
            )
            if self.has_pillow:
                try:
                    self.calibration_image = Image.open(file_path)
                    self._load_gif_frames()
                except Exception:
                    self.selected_image_path.set("Error loading GIF.")
                    self.calibration_image = None
                    self.gif_frames = []
                    self.gif_delays = []
            else:
                self.selected_image_path.set(
                    "Pillow not installed. Using white dot."
                )
                self.calibration_image = None

    def _load_gif_frames(self):
        self.gif_frames = []
        self.gif_delays = []
        try:
            while True:
                delay = self.calibration_image.info.get("duration", 100)
                self.gif_delays.append(delay)
                frame = self.calibration_image.convert("RGBA")
                self.gif_frames.append(ImageTk.PhotoImage(frame))
                self.calibration_image.seek(len(self.gif_frames))
        except EOFError:
            pass

    # ---------------------------------
    # Calibration workflow
    # ---------------------------------
    def _prepare_and_start_calibration(self):
        if not self.eye_tracker:
            self._show_message(
                "Eye tracker not connected. Please ensure it's on.",
                lambda: self._exit_app(),
            )
            return

        # Unsubscribe from headbox data during calibration
        if self.gaze_data_subscription:
            try:
                self.eye_tracker.unsubscribe_from(
                    tobii_research.EYETRACKER_GAZE_DATA,
                    self._update_headbox_guide,
                )
                self.gaze_data_subscription = None
            except Exception as e:
                print(f"Error unsubscribing from gaze data: {e}")

        self.setup_frame.pack_forget()
        self.master.attributes("-fullscreen", True)

        # Choose calibration pattern
        if self.point_choice.get() == "5":
            self.calibration_points = self.five_points
        else:
            self.calibration_points = self.nine_points

        try:
            self.calibration_object = tobii_research.ScreenBasedCalibration(
                self.eye_tracker
            )
            self.calibration_object.enter_calibration_mode()
            print("Entered calibration mode.")
            self.is_calibration_active = True
        except Exception as e:
            self._show_message(
                f"Error starting calibration: {e}", lambda: self._exit_app()
            )
            self.is_calibration_active = False
            return

        self.recalibrating_single_point = False
        self._initiate_calibration_process()

    def _show_message(self, message, callback=None):
        if self._message_box:
            self._message_box.destroy()

        self._message_box = tk.Toplevel(self.master)
        self._message_box.title("Message")
        self._message_box.transient(self.master)
        self._message_box.grab_set()
        self._message_box.geometry("400x150+200+200")
        self._message_box.configure(bg="#333")

        msg_label = tk.Label(
            self._message_box,
            text=message,
            font=("Helvetica", 14),
            fg="white",
            bg="#333",
            wraplength=380,
        )
        msg_label.pack(pady=20, padx=10)

        ok_button = tk.Button(
            self._message_box,
            text="OK",
            command=lambda: self._close_message_box(callback),
            font=("Helvetica", 12),
            bg="#007bff",
            fg="white",
        )
        ok_button.pack(pady=10)

        self._message_box.bind("<Return>", lambda event: self._close_message_box(callback))

    def _close_message_box(self, callback=None):
        if self._message_box:
            self._message_box.grab_release()
            self._message_box.destroy()
            self._message_box = None
        if callback:
            callback()

    def _initiate_calibration_process(self):
        self.canvas = tk.Canvas(self.master, bg="black", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.master.bind("<Return>", lambda e: self._start_point_collection())
        self.master.bind("<Left>", lambda e: self._previous_point())
        self.master.bind("<Right>", lambda e: self._next_point())
        self.master.bind("<Escape>", lambda e: self._on_esc_during_calibration())

        self.master.after(300, self._show_next_calibration_point)

    def _show_next_calibration_point(self):
        if self.gif_animation_job:
            self.master.after_cancel(self.gif_animation_job)
            self.gif_animation_job = None

        if 0 <= self.current_point_index < len(self.calibration_points):
            self.canvas.delete("all")
            norm_x, norm_y = self.calibration_points[self.current_point_index]
            pixel_x = norm_x * self.width
            pixel_y = norm_y * self.height

            if self.gif_frames:
                self.gif_frame_index = 0
                self.canvas_image_id = self.canvas.create_image(
                    pixel_x,
                    pixel_y,
                    image=self.gif_frames[self.gif_frame_index],
                    tags="calibration_point",
                )
                self._animate_gif()
            else:
                point_size = 15
                self.canvas.create_oval(
                    pixel_x - point_size,
                    pixel_y - point_size,
                    pixel_x + point_size,
                    pixel_y + point_size,
                    fill="white",
                    outline="white",
                    tags="calibration_point",
                )
        else:
            self._finish_calibration()

    def _next_point(self):
        if self.current_point_index < len(self.calibration_points) - 1:
            self.current_point_index += 1
            self._show_next_calibration_point()

    def _previous_point(self):
        if self.current_point_index > 0:
            self.current_point_index -= 1
            self._show_next_calibration_point()

    def _animate_gif(self):
        if self.gif_frames:
            frame = self.gif_frames[self.gif_frame_index]
            self.canvas.itemconfig(self.canvas_image_id, image=frame)
            delay = self.gif_delays[self.gif_frame_index]
            self.gif_frame_index = (self.gif_frame_index + 1) % len(self.gif_frames)
            self.gif_animation_job = self.master.after(delay, self._animate_gif)

    def _start_point_collection(self):
        norm_x, norm_y = self.calibration_points[self.current_point_index]
        print(f"Starting data collection for point {self.current_point_index + 1}...")
        # Use FIXATION_DURATION setting
        self.master.after(
            FIXATION_DURATION, lambda: self._collect_calibration_data(norm_x, norm_y)
        )

    def _collect_calibration_data(self, norm_x, norm_y):
        if self.calibration_object:
            try:
                success = self.calibration_object.collect_data(norm_x, norm_y)
                print(f"Collected data for ({norm_x:.2f}, {norm_y:.2f}): {success}")
                
                if success:
                    # If we are just fixing one point, go back to results immediately
                    if self.recalibrating_single_point:
                        self.recalibrating_single_point = False
                        self._finish_calibration()
                    # Normal flow: go to next point
                    elif self.current_point_index < len(self.calibration_points) - 1:
                        self.current_point_index += 1
                        self._show_next_calibration_point()
                    else:
                        print("Last calibration point complete. Processing...")
                        self._finish_calibration()
                else:
                    self._show_message("Data was not optimal. Try again.")
            except Exception as e:
                self._show_message(
                    f"Error collecting data: {e}",
                    lambda: self._on_esc_during_calibration(),
                )
        else:
            self._show_message(
                "Calibration not initialized.",
                lambda: self._on_esc_during_calibration(),
            )

    def _on_esc_during_calibration(self):
        self._finish_calibration()

    def _finish_calibration(self):
        if self.gif_animation_job:
            self.master.after_cancel(self.gif_animation_job)
            self.gif_animation_job = None

        self.canvas.delete("all")
        self.canvas.create_text(
            self.width / 2,
            self.height / 2,
            text="Processing results...",
            fill="white",
            font=("Helvetica", 24),
            anchor="center",
        )
        self.master.update()

        if self.calibration_object:
            try:
                result = self.calibration_object.compute_and_apply()
                print(f"Calibration success: {result.status}")
                
                # FIX: Even if status is failed/unsatisfactory, show what we have 
                # so user can see the results or choose to recalibrate. 
                # We only exit if it crashes completely.
                if hasattr(result, 'status') and result.status:
                     self.calibration_data = self.eye_tracker.retrieve_calibration_data()
                     # Save single file with all data
                     self._save_calibration_data()
                     self._draw_calibration_results(result)
                else:
                    # Show warning but try to show results anyway (if points exist)
                    if hasattr(result, 'calibration_points') and result.calibration_points:
                        self._show_message("Calibration result suboptimal.", 
                                           lambda: self._draw_calibration_results(result))
                    else:
                        self._show_message("Calibration failed (No data).", lambda: self._exit_app())

            except Exception as e:
                # If compute failed entirely, try to recover or show error
                print(f"Compute error: {e}")
                self._show_message(
                    f"Error applying calibration: {e}", 
                    lambda: self._exit_app() # Fatal error
                )
        else:
            self._show_message(
                "No calibration object found.", lambda: self._exit_app()
            )

    def _draw_calibration_results(self, result):
        self.canvas.delete("all")
        
        # Bind keys for exiting
        self.master.bind("<Escape>", self._exit_app)
        
        # Bind click for re-calibrating a point
        self.canvas.bind("<Button-1>", self._on_results_click)

        for idx, cp in enumerate(result.calibration_points):
            norm_x, norm_y = cp.position_on_display_area
            px = int(norm_x * self.width)
            py = int(norm_y * self.height)

            # Draw white cross at target (clickable area)
            self.canvas.create_line(px - 10, py, px + 10, py, fill="white", width=2)
            self.canvas.create_line(px, py - 10, px, py + 10, fill="white", width=2)
            
            # Create an invisible hit box for easier clicking
            self.canvas.create_rectangle(px-30, py-30, px+30, py+30, fill="", outline="", tags=f"pt_{idx}")

            for sample in cp.calibration_samples:
                # Draw left eye
                if (hasattr(sample, "left_eye") and sample.left_eye 
                        and hasattr(sample.left_eye, "position_on_display_area")):
                    lx, ly = sample.left_eye.position_on_display_area
                    if lx == lx and ly == ly: # NaN check
                        lx_px = int(lx * self.width)
                        ly_px = int(ly * self.height)
                        
                        # COLOR FIX: Left = Green if valid, else Red
                        validity = str(getattr(sample.left_eye, "validity", "")).lower()
                        is_valid = "valid" in validity or validity == "1"
                        color = "green" if is_valid else "red"
                        
                        self.canvas.create_oval(lx_px - 4, ly_px - 4, lx_px + 4, ly_px + 4, fill=color, outline="")

                # Draw right eye
                if (hasattr(sample, "right_eye") and sample.right_eye 
                        and hasattr(sample.right_eye, "position_on_display_area")):
                    rx, ry = sample.right_eye.position_on_display_area
                    if rx == rx and ry == ry: # NaN check
                        rx_px = int(rx * self.width)
                        ry_px = int(ry * self.height)

                        # COLOR FIX: Right = Blue if valid, else Red
                        validity = str(getattr(sample.right_eye, "validity", "")).lower()
                        is_valid = "valid" in validity or validity == "1"
                        color = "blue" if is_valid else "red"

                        self.canvas.create_oval(rx_px - 4, ry_px - 4, rx_px + 4, ry_px + 4, fill=color, outline="")

        self.canvas.create_text(
            self.width // 2,
            self.height - 40,
            text="CLICK any point to RE-CALIBRATE it.",
            fill="#00ff00",
            font=("Helvetica", 16, "bold"),
        )
        self.canvas.create_text(
            self.width // 2,
            40,
            text="Press ESC to Save & Exit",
            fill="white",
            font=("Helvetica", 18),
        )

    def _on_results_click(self, event):
        # Determine which point was clicked
        click_x, click_y = event.x, event.y
        closest_idx = -1
        min_dist = float('inf')
        
        for i, pt in enumerate(self.calibration_points):
            norm_x, norm_y = pt
            px = norm_x * self.width
            py = norm_y * self.height
            dist = math.hypot(px - click_x, py - click_y)
            if dist < 50: # 50px threshold
                if dist < min_dist:
                    min_dist = dist
                    closest_idx = i
        
        if closest_idx != -1:
            print(f"Re-calibrating point {closest_idx}")
            self.current_point_index = closest_idx
            self.recalibrating_single_point = True
            self.master.unbind("<Escape>") # Unbind exit so we don't close app during re-calib
            self.master.bind("<Escape>", lambda e: self._on_esc_during_calibration())
            self._show_next_calibration_point()

    def _save_calibration_data(self):
        if self.calibration_data:
            from datetime import datetime

            enhanced_metadata = {
                "timestamp": datetime.now().isoformat(),
                "eyetracker_serial": self.eye_tracker.serial_number,
                "eyetracker_model": self.eye_tracker.model,
                "screen_width_px": self.width,
                "screen_height_px": self.height,
                # Important: the exact normalized calibration points used
                "calibration_points_normalized": self.calibration_points,
                # Binary blob included in the PKL now
                "calibration_data_binary": self.calibration_data,
            }

            try:
                with open("tobii_calibration.pkl", "wb") as f:
                    pickle.dump(enhanced_metadata, f)
                print("Calibration saved to tobii_calibration.pkl")
            except Exception as e:
                print(f"Error saving pickle: {e}")

    def _exit_app(self, event=None):
        self.is_exiting = True
        threading.Thread(target=self._cleanup_and_exit, daemon=True).start()

    def _cleanup_and_exit(self):
        try:
            if self.gaze_data_subscription and self.eye_tracker:
                self.eye_tracker.unsubscribe_from(
                    tobii_research.EYETRACKER_GAZE_DATA, self._update_headbox_guide
                )
            if self.calibration_object and self.is_calibration_active:
                self.calibration_object.leave_calibration_mode()
        except Exception as e:
            print(f"Cleanup error: {e}")
        self.master.after(100, self._destroy)

    def _destroy(self):
        try:
            if self.gif_animation_job:
                self.master.after_cancel(self.gif_animation_job)
            self.master.quit()
            self.master.destroy()
            sys.exit(0)
        except Exception:
            sys.exit(0)


if __name__ == "__main__":
    print("=" * 60)
    print("Tobii Eye Tracker Calibration")
    print("=" * 60)
    print("NOTE: This software requires the Tobii Pro SDK,")
    print("which must be installed separately from Tobii AB.")
    print("  https://www.tobiipro.com/product-listing/tobii-pro-sdk/")
    print("See README.md for full installation instructions.")
    print("=" * 60)
    root = tk.Tk()
    app = CalibrationApp(root)
    root.mainloop()