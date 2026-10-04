import os
import re
import sys
import io
import queue
import threading
import subprocess
import multiprocessing
import traceback

# --- Fix --noconsole crashes: libraries that print need a stdout/stderr ---
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")


def _preload_onnxruntime_dlls():
    """In the built .exe, load onnxruntime's own DLLs BEFORE Pillow/numpy can load
    conflicting copies (e.g. msvcp140.dll). Must run before any other imports."""
    if not (getattr(sys, "frozen", False) and sys.platform == "win32"):
        return
    base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    capi = os.path.join(base, "onnxruntime", "capi")
    if not os.path.isdir(capi):
        return
    try:
        os.add_dll_directory(capi)
    except Exception:
        pass
    os.environ["PATH"] = capi + os.pathsep + os.environ.get("PATH", "")
    import ctypes
    names = sorted(os.listdir(capi))
    runtimes = [n for n in names if n.lower().startswith(("vcruntime", "msvcp")) and n.lower().endswith(".dll")]
    for name in runtimes + ["onnxruntime_providers_shared.dll", "onnxruntime.dll"]:
        path = os.path.join(capi, name)
        if os.path.exists(path):
            try:
                ctypes.WinDLL(path)
            except OSError:
                pass  # the real error is reported later by `import onnxruntime`


_preload_onnxruntime_dlls()

from PIL import Image, ImageOps, ImageFilter
import customtkinter as ctk
from tkinterdnd2 import DND_FILES, TkinterDnD

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

SUPPORTED_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
AI_MODEL = "u2net"          # explicit model (new rembg defaults to a much heavier one)
MAX_WORK_SIDE = 2000        # downscale huge camera photos before AI (final output is <=1200px)


# ----------------------------------------------------------------------------
# Paths (work both as .py and as a PyInstaller build)
# ----------------------------------------------------------------------------
def resource_path(rel):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def app_data_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "PhotoResizerPro")
    os.makedirs(path, exist_ok=True)
    return path


def model_exists(home):
    name = f"{AI_MODEL}.onnx"
    return any(os.path.exists(p) for p in (os.path.join(home, name),
                                           os.path.join(home, "models", AI_MODEL, name)))


def configure_ai_environment():
    """Must run BEFORE rembg is imported. Returns the folder holding the model."""
    bundled = resource_path("models")
    if model_exists(bundled):
        os.environ["U2NET_HOME"] = bundled          # model shipped inside the build
    else:
        user_models = os.path.join(app_data_dir(), "models")
        os.makedirs(user_models, exist_ok=True)
        os.environ.setdefault("U2NET_HOME", user_models)
    # rembg imports pymatting (numba) even though we never use alpha matting.
    # In a frozen .exe numba's JIT cache breaks the import; disabling JIT fixes
    # that and makes the import much faster. No effect on our results.
    os.environ["NUMBA_DISABLE_JIT"] = "1"
    return os.environ["U2NET_HOME"]


# ----------------------------------------------------------------------------
# AI engine: loaded ONCE in the background right after the window opens
# ----------------------------------------------------------------------------
class AIEngine:
    def __init__(self):
        self.ready = threading.Event()
        self.remove = None
        self.session = None
        self.error = None
        self.error_details = ""

    def start(self, log, on_error=None):
        threading.Thread(target=self._load, args=(log, on_error), daemon=True).start()

    def _load(self, log, on_error=None):
        try:
            model_home = configure_ai_environment()
            if not model_exists(model_home):
                log("First run: downloading AI model (~170 MB). Only happens once, please wait...")
            else:
                log("Loading AI background remover...")
            # Import onnxruntime ourselves first: if it fails, rembg would hide the
            # real reason behind sys.exit(1) ("SystemExit: 1").
            import onnxruntime  # noqa: F401
            from rembg import remove, new_session
            self.session = new_session(AI_MODEL)
            self.remove = remove
            log("AI engine ready.")
        except BaseException as e:
            self.error = e
            self.error_details = traceback.format_exc()
            log_file = os.path.join(app_data_dir(), "error_log.txt")
            try:
                with open(log_file, "w", encoding="utf-8") as f:
                    f.write(self.error_details)
            except Exception:
                log_file = "(could not write log file)"
            log("❌ AI background remover FAILED to load:")
            for line in self.error_details.strip().splitlines()[-6:]:
                log("   " + line)
            log(f"Full error saved to: {log_file}")
            if on_error:
                on_error(f"{type(e).__name__}: {e}", log_file)
        finally:
            self.ready.set()


# ----------------------------------------------------------------------------
# Image processing (pure functions, no UI calls -> safe in a worker thread)
# ----------------------------------------------------------------------------
def parse_base_name(file_path):
    """ALT0008-Photoroom.png -> ALT0008"""
    name = os.path.splitext(os.path.basename(file_path))[0]
    base = re.split(r"[-_]", name)[0].strip()
    return base or name


def load_image(path):
    with Image.open(path) as im:
        im.load()
        img = ImageOps.exif_transpose(im)   # respect phone camera rotation
    if img.mode not in ("RGB", "RGBA"):
        has_alpha = "A" in img.mode or "transparency" in img.info
        img = img.convert("RGBA" if has_alpha else "RGB")
    if max(img.size) > MAX_WORK_SIDE:
        img.thumbnail((MAX_WORK_SIDE, MAX_WORK_SIDE), Image.Resampling.LANCZOS)
    return img


def trim_transparency(img, padding=15, alpha_threshold=150):
    """Crop to visible content and hard-cut semi-transparent edge fringing."""
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    lut = [255 if p > alpha_threshold else 0 for p in range(256)]
    r, g, b, a = img.split()
    a_clean = a.point(lut)
    bbox = a_clean.getbbox()
    if not bbox:
        return img
    img = Image.merge("RGBA", (r, g, b, a_clean))
    left, top, right, bottom = bbox
    w, h = img.size
    return img.crop((max(0, left - padding), max(0, top - padding),
                     min(w, right + padding), min(h, bottom + padding)))


def flatten_on_white(img):
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, (0, 0), img)
        return bg
    return img.convert("RGB")


def resize_constrained(img, max_w=330, max_h=560):
    w, h = img.size
    scale = min(max_w / w, max_h / h)
    return img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)


SHARPEN = ImageFilter.UnsharpMask(radius=1.0, percent=120, threshold=3)


def _render_square(img, dim):
    w, h = img.size
    scale = min(dim / w, dim / h)
    sw, sh = max(1, int(w * scale)), max(1, int(h * scale))
    scaled = img.resize((sw, sh), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (dim, dim), (255, 255, 255))
    pos = ((dim - sw) // 2, (dim - sh) // 2)
    if scaled.mode == "RGBA":
        canvas.paste(scaled, pos, scaled)
    else:
        canvas.paste(scaled, pos)
    return canvas.filter(SHARPEN)


def _encode_jpeg(img, quality):
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def square_under_kb(img, target_kb=29.9, quality=90):
    """Largest square (10px steps) whose JPEG is <= target_kb. Binary search = few encodes."""
    target = int(target_kb * 1024)
    top = min(1200, max(1000, max(img.size)))
    lo, hi = 5, top // 10            # dims 50 .. top, in steps of 10
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        dim = mid * 10
        data = _encode_jpeg(_render_square(img, dim), quality)
        if len(data) <= target:
            best = (dim, data)
            lo = mid + 1
        else:
            hi = mid - 1
    if best is None:                  # nothing fit; return the smallest
        dim = 50
        best = (dim, _encode_jpeg(_render_square(img, dim), quality))
    return best


def unique_name(base, used):
    name, n = base, 2
    while name.lower() in used:
        name = f"{base}_{n}"
        n += 1
    used.add(name.lower())
    return name


# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------
class TkinterDnDCTk(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.TkdndVersion = TkinterDnD._require(self)


class PhotoResizerApp(TkinterDnDCTk):
    def __init__(self):
        super().__init__()
        self.title("PhotoResizer Pro")
        self.geometry("820x680")
        self.minsize(750, 600)
        self.set_app_icon()

        self.files_to_process = []
        self.is_processing = False
        self.ui_queue = queue.Queue()     # worker threads talk to the UI only through this
        self.engine = AIEngine()

        self.setup_ui()
        self.after(50, self._poll_queue)
        # Show the window first, then warm up the AI in the background
        self.after(300, lambda: self.engine.start(
            self.post_log, lambda msg, path: self.ui_queue.put(("ai_error", (msg, path)))))

    # ---- thread-safe messaging -------------------------------------------
    def post_log(self, msg):
        self.ui_queue.put(("log", msg))

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "log":
                    self.log(payload)
                elif kind == "progress":
                    self.progress_bar.set(payload)
                elif kind == "ai_error":
                    msg, path = payload
                    from tkinter import messagebox
                    messagebox.showerror(
                        "Background removal unavailable",
                        "The AI background remover failed to load, so photos will only be "
                        f"resized (no background removal, no crop).\n\nError: {msg}\n\n"
                        f"Full details saved to:\n{path}")
                elif kind == "done":
                    self._on_batch_done(*payload)
        except queue.Empty:
            pass
        self.after(50, self._poll_queue)

    # ---- setup ---------------------------------------------------------------
    def set_app_icon(self):
        if sys.platform == "win32":
            try:  # makes Windows show our icon in the taskbar instead of Python's
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PhotoResizerPro.App")
            except Exception:
                pass
        icon = resource_path("icon.ico")
        if os.path.exists(icon):
            try:
                self.iconbitmap(icon)
            except Exception:
                pass

    def setup_ui(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(15, 5))
        ctk.CTkLabel(header, text="PhotoResizer Pro",
                     font=ctk.CTkFont(size=24, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(header,
                     text="Drag & drop product images to auto-remove background, crop to content, resize, and generate square copies.",
                     font=ctk.CTkFont(size=13), text_color="gray").pack(anchor="w")

        self.drop_frame = ctk.CTkFrame(self, corner_radius=12, border_width=2,
                                       border_color="#3B82F6", fg_color="#1E293B")
        self.drop_frame.pack(fill="x", padx=20, pady=10)
        self.drop_frame.drop_target_register(DND_FILES)
        self.drop_frame.dnd_bind("<<Drop>>", self.handle_drop)

        ctk.CTkLabel(self.drop_frame, text="📁 Drag & Drop Photos Here\nOR",
                     font=ctk.CTkFont(size=16, weight="bold"),
                     text_color="#94A3B8").pack(pady=(20, 5))
        self.browse_btn = ctk.CTkButton(self.drop_frame, text="Browse Files", fg_color="#2563EB",
                                        hover_color="#1D4ED8", width=140, command=self.browse_files)
        self.browse_btn.pack(pady=(0, 20))

        settings = ctk.CTkFrame(self, corner_radius=10)
        settings.pack(fill="x", padx=20, pady=5)
        self.auto_open_var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(settings, text="Auto-open output folder when done",
                      variable=self.auto_open_var,
                      font=ctk.CTkFont(size=13)).pack(side="left", padx=15, pady=12)

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=20, pady=10)
        self.start_btn = ctk.CTkButton(actions, text="Start Processing",
                                       font=ctk.CTkFont(size=14, weight="bold"),
                                       fg_color="#10B981", hover_color="#059669", height=38,
                                       command=self.start_batch_thread)
        self.start_btn.pack(side="left", padx=(0, 10))
        self.clear_btn = ctk.CTkButton(actions, text="Clear Queue", font=ctk.CTkFont(size=13),
                                       fg_color="#EF4444", hover_color="#DC2626", height=38,
                                       width=100, command=self.clear_queue)
        self.clear_btn.pack(side="left")
        self.status_label = ctk.CTkLabel(actions, text="0 files selected",
                                         font=ctk.CTkFont(size=13), text_color="gray")
        self.status_label.pack(side="right", padx=10)

        self.progress_bar = ctk.CTkProgressBar(self)
        self.progress_bar.pack(fill="x", padx=20, pady=(0, 10))
        self.progress_bar.set(0)

        self.log_box = ctk.CTkTextbox(self, corner_radius=10,
                                      font=ctk.CTkFont(family="Consolas", size=12),
                                      fg_color="#0F172A", text_color="#38BDF8")
        self.log_box.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        self.log("Ready. Drop files above to begin.")

    # ---- UI actions (main thread only) ------------------------------------
    def log(self, message):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"> {message}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def handle_drop(self, event):
        files = [f for f in self.tk.splitlist(event.data) if f.lower().endswith(SUPPORTED_EXT)]
        if files:
            self.add_files(files)
        else:
            self.log("Warning: No supported image files found in drop.")

    def browse_files(self):
        files = ctk.filedialog.askopenfilenames(
            title="Select Product Photos",
            filetypes=[("Image Files", "*.png *.jpg *.jpeg *.webp *.bmp")])
        if files:
            self.add_files(files)

    def add_files(self, file_paths):
        added = 0
        for path in file_paths:
            path = os.path.normpath(path)
            if path not in self.files_to_process:
                self.files_to_process.append(path)
                added += 1
        count = len(self.files_to_process)
        self.status_label.configure(text=f"{count} file(s) selected")
        self.log(f"Added {added} image(s) to queue. Total: {count}")

    def clear_queue(self):
        if self.is_processing:
            return
        self.files_to_process.clear()
        self.status_label.configure(text="0 files selected")
        self.progress_bar.set(0)
        self.log("Queue cleared.")

    def set_controls(self, enabled):
        state = "normal" if enabled else "disabled"
        for btn in (self.start_btn, self.clear_btn, self.browse_btn):
            btn.configure(state=state)

    def start_batch_thread(self):
        if self.is_processing:
            return
        if not self.files_to_process:
            self.log("Error: No files selected to process.")
            return
        self.is_processing = True
        self.set_controls(False)
        self.progress_bar.set(0)
        files = list(self.files_to_process)        # snapshot: worker never touches UI state
        auto_open = self.auto_open_var.get()       # read Tk variable on the main thread
        threading.Thread(target=self.process_batch, args=(files, auto_open), daemon=True).start()

    def _on_batch_done(self, output_dir, auto_open):
        self.is_processing = False
        self.set_controls(True)
        if auto_open and output_dir and os.path.isdir(output_dir):
            try:
                os.startfile(output_dir)
            except Exception:
                subprocess.Popen(["explorer", output_dir])

    # ---- worker thread (NO direct widget access in here) ------------------
    def process_batch(self, files, auto_open):
        log = self.post_log
        output_dir = None
        try:
            total = len(files)
            log(f"--- Starting Batch Process ({total} files) ---")
            output_dir = os.path.join(os.path.dirname(files[0]), "Resized_Output")
            os.makedirs(output_dir, exist_ok=True)

            if not self.engine.ready.is_set():
                log("Waiting for AI engine to finish loading...")
            self.engine.ready.wait()
            use_ai = self.engine.session is not None
            if not use_ai:
                log("⚠ BACKGROUND REMOVAL IS OFF (AI failed to load) - only resizing.")

            used_names = set()
            for idx, path in enumerate(files, start=1):
                filename = os.path.basename(path)
                base = unique_name(parse_base_name(path), used_names)
                log(f"[{idx}/{total}] {filename} -> '{base}'")
                try:
                    img = load_image(path)

                    if use_ai:
                        log("  └ Removing background...")
                        img = self.engine.remove(img, session=self.engine.session)
                        img = trim_transparency(img)

                    main = flatten_on_white(resize_constrained(img)).filter(SHARPEN)
                    main.save(os.path.join(output_dir, f"{base}.jpg"), "JPEG", quality=92, optimize=True)
                    log(f"  ├ Saved Main: {base}.jpg ({main.width}x{main.height}px)")

                    dim, data = square_under_kb(img, target_kb=29.9, quality=90)
                    with open(os.path.join(output_dir, f"{base} - copy.jpg"), "wb") as f:
                        f.write(data)
                    log(f"  └ Saved Square: {base} - copy.jpg ({dim}x{dim}px, {len(data) / 1024:.1f} KB)")
                except Exception as e:
                    log(f"  ❌ Error processing {filename}: {e}")

                self.ui_queue.put(("progress", idx / total))

            log("--- Batch Processing Complete! ---")
            log(f"Output saved to: {output_dir}")
        except Exception as e:
            log(f"❌ Batch failed: {e}")
        finally:
            self.ui_queue.put(("done", (output_dir, auto_open)))


def run_selftest():
    """PhotoResizerPro.exe --selftest : loads the AI engine without the window and
    writes the result next to the exe. Used by build.bat to verify the build."""
    out = os.path.join(os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__)),
                       "selftest_result.txt")
    lines = []
    engine = AIEngine()
    engine._load(lines.append)
    ok = engine.session is not None
    text = ("SELFTEST OK" if ok else "SELFTEST FAILED") + "\n" + "\n".join(lines) + "\n"
    if engine.error_details:
        text += "\n" + engine.error_details
    with open(out, "w", encoding="ascii", errors="ignore") as f:   # plain text for the console
        f.write(text)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if "--selftest" in sys.argv:
        run_selftest()
    app = PhotoResizerApp()
    app.mainloop()