# Video Connector v1.0
# Copyright (c) 2025 Eca & ChatGPT
# Licensed under the MIT License. See LICENSE for details.



import tkinter as tk
from tkinter import filedialog, messagebox, Listbox, Scrollbar
import subprocess, os, tempfile, shutil, json, re, sys
from subprocess import CREATE_NO_WINDOW
import threading, queue
import time
import logging




cancel_flag = threading.Event()


# Basic logging setup (creates a file named video_connector.log)
logging.basicConfig(
    filename="video_connector.log",
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8"
)

APP_NAME = "Video Connector"
APP_VERSION = "v1.0"


file_entries = []  # [(path, display_name), ...]
ui = {}  # stores GUI references


try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    DND_FILES = None
    TkinterDnD = None
    print("⚠ Drag & Drop not available (install with: pip install tkinterdnd2)")


# -------- helpers --------
def run_cmd(cmd):
    """Run a command silently (no console popup)."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0
    )


def ffprobe_video_params(path):
    """Get width, height, fps, bitrate, and audio presence safely."""
    v = run_cmd([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,bit_rate",
        "-of", "json", path
    ])

    width = height = fps = bitrate = None
    has_audio = False
    data = {}

    if v.returncode == 0 and v.stdout:
        try:
            data = json.loads(v.stdout)
        except json.JSONDecodeError:
            data = {}

        if data.get("streams"):
            s = data["streams"][0]
            width = int(s.get("width", 0) or 0)
            height = int(s.get("height", 0) or 0)
            bitrate = s.get("bit_rate")
            if bitrate:
                bitrate = int(bitrate) // 1000  # kbps
            r = s.get("r_frame_rate", "0/1")
            if "/" in r:
                n, d = r.split("/")
                try:
                    fps = round(float(n) / float(d), 2)
                except Exception:
                    fps = None
        else:
            # No streams -> invalid or unsupported file
            return {"width": None, "height": None, "fps": None, "bitrate": None, "has_audio": False}

    # --- audio check ---
    a = run_cmd([
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_type",
        "-of", "csv=p=0", path
    ])
    has_audio = (a.returncode == 0 and a.stdout.strip() != "")

    return {
        "width": width,
        "height": height,
        "fps": fps,
        "bitrate": bitrate,
        "has_audio": has_audio,
    }




def nearest_common_fps(fps):
    if not fps:
        return 30
    return min([24, 25, 30, 60], key=lambda x: abs(x - fps))


def norm_path_for_listfile(p):
    return os.path.abspath(p).replace("\\", "/")


def normalize_clip(src, dst, w, h, fps, ensure_audio=True, crf=18):
    src = os.path.abspath(src)
    dst = os.path.abspath(dst)
    info = ffprobe_video_params(src)

    vf = f"scale={w}:{h}:flags=lanczos,format=yuv420p"
    cmd = ["ffmpeg", "-y", "-i", src]

    if ensure_audio and not info["has_audio"]:
        cmd += [
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-shortest", "-map", "0:v:0", "-map", "1:a:0"
        ]

    cmd += [
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
        "-profile:v", "high", "-level:v", "4.1",
        "-pix_fmt", "yuv420p",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-c:a", "aac", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", dst
    ]

    p = subprocess.run(cmd, capture_output=True, text=True,
                       creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0)
    if p.returncode != 0:
        raise RuntimeError(f"Normalization failed for {os.path.basename(src)}:\n\n{p.stderr}")

def color_code_fps(fps_value, base_fps):
    """Return color based on framerate difference."""
    if base_fps is None or fps_value is None:
        return "#444"  # unknown → neutral gray
    diff = abs(fps_value - base_fps)
    if diff > 0.1:
        return "red"   # mismatch
    return "#444"      # match

    
def add_files():
    files = filedialog.askopenfilenames(
        title="Select Video Files",
        filetypes=[("Video files", "*.mp4 *.mov *.mkv *.avi *.webm *.flv *.wmv *.m4v")]
    )
    for path in files:
        info = ffprobe_video_params(path)
        if not info["width"] or not info["height"] or not info["fps"]:
            messagebox.showwarning(
                "Invalid or Corrupted File",
                f"The file:\n\n{os.path.basename(path)}\n\n"
                "does not appear to be a valid playable video.\n"
                "It will be skipped."
            )
            continue
        add_to_list(path)





def remove_selected():
    global file_entries
    sel = list(file_listbox.curselection())
    for i in reversed(sel):
        file_listbox.delete(i)
        del file_entries[i]

def clear_all():
    global file_entries
    file_listbox.delete(0, tk.END)
    file_entries.clear()



def move_up():
    global file_entries
    sel = file_listbox.curselection()
    if not sel or sel[0] == 0:
        return
    i = sel[0]
    file_entries[i - 1], file_entries[i] = file_entries[i], file_entries[i - 1]
    refresh_listbox(i - 1)

def move_down():
    global file_entries
    sel = file_listbox.curselection()
    if not sel or sel[0] == len(file_entries) - 1:
        return
    i = sel[0]
    file_entries[i + 1], file_entries[i] = file_entries[i], file_entries[i + 1]
    refresh_listbox(i + 1)

def refresh_listbox(select_index=None):
    file_listbox.delete(0, tk.END)
    for _, display in file_entries:
        file_listbox.insert(tk.END, display)
    if select_index is not None:
        file_listbox.selection_set(select_index)


def join_videos_threaded():
    """Run join_videos() in a background thread so GUI stays responsive."""
    if cancel_flag.is_set():
        cancel_flag.clear()
    t = threading.Thread(target=join_videos, daemon=True)
    t.start()


def join_videos():
    vids = [p for p, _ in file_entries]
    if not vids:
        messagebox.showerror("No Files", "Please add at least one video.")
        return

    out_str = output_path.get().strip()
    if not out_str:
        messagebox.showerror("No Output", "Please choose an output file path.")
        return

    from pathlib import Path
    out_file = Path(out_str).resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)

    first = ffprobe_video_params(vids[0])
    w, h = first["width"], first["height"]
    fps = nearest_common_fps(first["fps"])
    tmp = tempfile.mkdtemp(prefix="join_norm_")
    normalized = []

    try:
        total_steps = len(vids) + 1
        progress['value'] = 0
        status.set("Starting…")
        progress_value.set("0%")
        progress_label.config(fg="#555")  # back to neutral grey

        progress_label.config(fg="#555")  # neutral grey


        for i, src in enumerate(vids, 1):
            if cancel_flag.is_set():
                raise RuntimeError("Cancelled by user.")
            dst = os.path.join(tmp, f"clip_{i:03d}.mp4")
            status.set(f"Normalizing clip {i}/{len(vids)}…")
            progress['value'] = (i - 1) / total_steps * 100
            root.update_idletasks()
            progress_value.set(f"{int(progress['value'])}%")
            progress_value.set(f"{int(progress['value'])}%")
            normalize_clip(src, dst, w, h, fps, ensure_audio=True)
            normalized.append(dst)

        listf = os.path.join(tmp, "list.txt")
        with open(listf, "w", encoding="utf-8") as f:
            for n in normalized:
                f.write(f"file '{norm_path_for_listfile(n)}'\n")

        status.set("Merging…")
        progress['value'] = (total_steps - 1) / total_steps * 100
        root.update_idletasks()
        progress_value.set(f"{int(progress['value'])}%")
        cmd = [
            "ffmpeg", "-hide_banner", "-nostdin", "-y",
            "-f", "concat", "-safe", "0", "-i", listf,
            "-fflags", "+genpts",
            "-vsync", "vfr",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-c:a", "aac", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart",
            str(out_file)
        ]

        q = queue.Queue()

        def reader(proc, q):
            for line in proc.stderr:
                q.put(line)
            proc.stderr.close()

        process = subprocess.Popen(
            cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True
        )
        threading.Thread(target=reader, args=(process, q), daemon=True).start()

        while process.poll() is None:
            if cancel_flag.is_set():
                process.terminate()
                raise RuntimeError("Cancelled by user.")
            try:
                while True:
                    line = q.get_nowait()
                    if "frame=" in line:
                        progress.step(0.5)
                    logging.debug(line.strip())
            except queue.Empty:
                pass
            root.update_idletasks()
            time.sleep(0.2)

        if process.returncode != 0:
            raise RuntimeError("FFmpeg returned error.")

        progress['value'] = 100
        status.set("✅ Done!")
        progress_value.set("100%")
        progress_label.config(fg="#2ecc71")  # green

        messagebox.showinfo("Success", f"Joined {len(vids)} clips → {out_file}")
        


    except Exception as e:
        status.set(f"❌ {e}")
        messagebox.showerror("Error", str(e))
        progress_label.config(fg="#e74c3c")  # red

    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        progress['value'] = 0
        cancel_flag.clear()



def handle_drop(event):
    global file_entries
    files = root.splitlist(event.data)
    for f in files:
        if os.path.isfile(f):
            ext = os.path.splitext(f.lower())[1]
            if ext in [".mp4", ".mov", ".mkv", ".avi"]:
               info = ffprobe_video_params(f)
               fps_text = f"{info['fps']:.2f}fps" if info["fps"] else "?"
               br_text = f"{info['bitrate']}kbps" if info["bitrate"] else "?"
               display_name = f"{os.path.basename(f)}  —  {fps_text}  —  {br_text}"

    # compare to base fps (first file)
    if file_entries:
        base_fps = ffprobe_video_params(file_entries[0][0])['fps']
    else:
        base_fps = info['fps']

    color = color_code_fps(info['fps'], base_fps)
    idx = file_listbox.size()
    file_listbox.insert(tk.END, display_name)
    file_listbox.itemconfig(idx, {'fg': color})
    file_entries.append((f, display_name))





# -------- GUI setup --------
if TkinterDnD:
    root = TkinterDnD.Tk()
else:
    root = tk.Tk()
    
    
    
import shutil, webbrowser

def ensure_ffmpeg():
    """Check if FFmpeg/FFprobe are installed, offer to open download page."""
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return True

    resp = messagebox.askyesno(
        "FFmpeg Not Found",
        "FFmpeg or FFprobe could not be found on your system.\n\n"
        "This tool requires FFmpeg to process video files.\n\n"
        "Would you like to open the FFmpeg download page now?"
    )
    if resp:
        webbrowser.open("https://ffmpeg.org/download.html")
    root.destroy()
    return False

# ✅ Run the check before anything else
if not ensure_ffmpeg():
    sys.exit()


root.geometry("620x480")
root.configure(bg="#f0f0f0")
root.title(APP_NAME + APP_VERSION + " By Eca and ChatGPT")
root.geometry("620x480")

frame = tk.Frame(root)
frame.pack(padx=10, pady=10, fill="both", expand=True)
scroll = Scrollbar(frame)
scroll.pack(side="right", fill="y")

# --- after your listbox is created ---
file_listbox = Listbox(frame, selectmode=tk.SINGLE, yscrollcommand=scroll.set)
file_listbox.pack(fill="both", expand=True)
scroll.config(command=file_listbox.yview)

# ✅ define the open_selected() function right here
def open_selected(event):
    sel = file_listbox.curselection()
    if not sel:
        return
    path = file_entries[sel[0]][0]
    try:
        os.startfile(path)
        status.set(f"▶ Opening {os.path.basename(path)}…")
        ui["status_label"].config(fg="#444")
    except Exception as e:
        status.set(f"⚠ Could not open file: {e}")
        ui["status_label"].config(fg="red")


# ✅ now bind it to the listbox
file_listbox.bind("<Double-1>", open_selected)

# ✅ then continue with your drag-drop code
if DND_FILES:
    file_listbox.drop_target_register(DND_FILES)
    file_listbox.dnd_bind("<<Drop>>", handle_drop)


bframe = tk.Frame(root)
bframe.pack(pady=6)
tk.Button(bframe, text="Add Files", command=lambda: add_files()).grid(row=0, column=0, padx=5)
tk.Button(bframe, text="Remove", command=lambda: remove_selected()).grid(row=0, column=1, padx=5)
tk.Button(bframe, text="Up", command=lambda: move_up()).grid(row=0, column=2, padx=5)
tk.Button(bframe, text="Down", command=lambda: move_down()).grid(row=0, column=3, padx=5)
tk.Button(bframe, text="Clear All", command=clear_all, fg="white", bg="#c62828").grid(row=0, column=4, padx=5)

# --- Output file section ---
output_path = tk.StringVar()
tk.Label(root, text="Output File:").pack(anchor="w", padx=10, pady=(10, 0))
tk.Entry(root, textvariable=output_path, width=70).pack(padx=10)

def browse_output():
    path = filedialog.asksaveasfilename(
        defaultextension=".mp4",
        filetypes=[("MP4 files", "*.mp4")],
        title="Select output video file"
    )
    if path:
        output_path.set(path)

tk.Button(root, text="Browse", command=browse_output).pack(pady=5)

# --- Frame for buttons ---
pframe = tk.Frame(root, bg="#f0f0f0")
pframe.pack(pady=5)

join_btn = tk.Button(
    pframe, text="Normalize + Join", bg="#4CAF50", fg="white",
    command=join_videos_threaded
)
join_btn.pack(side="right", padx=4)

cancel_btn = tk.Button(
    pframe, text="Cancel", bg="#f44336", fg="white",
    command=lambda: cancel_flag.set()
)
cancel_btn.pack(side="right", padx=4)

# --- Progress bar ---
# --- Progress bar with percentage ---
from tkinter import ttk

# --- Progress bar with colored percentage ---
from tkinter import ttk

progress_frame = tk.Frame(root, bg="#f0f0f0")
progress_frame.pack(pady=4)

progress = ttk.Progressbar(progress_frame, length=360, mode="determinate")
progress.pack(side="left", padx=(10, 4))

progress_value = tk.StringVar(value="0%")
progress_label = tk.Label(
    progress_frame,
    textvariable=progress_value,
    width=6,
    anchor="w",
    bg="#f0f0f0",
    fg="#555",   # neutral grey
    font=("Segoe UI", 9, "bold")
)
progress_label.pack(side="left")


# --- Status label ---
status = tk.StringVar(value="Ready.")
ui["status_label"] = tk.Label(root, textvariable=status, fg="#444", bg="#f0f0f0", anchor="w")
ui["status_label"].pack(fill="x", padx=8, pady=(0, 8))




print("Launching GUI...")
root.mainloop()
