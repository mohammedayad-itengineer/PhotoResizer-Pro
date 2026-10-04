# PhotoResizer Pro

A desktop tool for batch-preparing product photos for e-commerce. It removes the background with AI, crops tightly around the product, and exports ready-to-upload JPGs in two sizes: a constrained main image and a square copy under 30 KB. Built for online stores and sellers who process many product images at once.

## Features

* Drag & drop or browse to add photos (`.png`, `.jpg`, `.jpeg`, `.webp`, `.bmp`)
* AI background removal (rembg / U²-Net), fully offline once the model is bundled
* Automatic crop to the product's edges, with clean-up of semi-transparent edge fringing
* Two outputs per photo:
  * **Main image**: fits within 330 × 560 px, white background, sharpened
  * **Square copy**: white square canvas (up to 1200 × 1200 px), automatically sized to stay under 30 KB
* Smart file naming from the filename prefix:
  * `ALT0008-Photoroom.png` → `ALT0008.jpg` and `ALT0008 - copy.jpg`
  * Duplicate prefixes in one batch get a suffix instead of overwriting (`ALT0008_2.jpg`)
* Respects phone camera rotation (EXIF orientation)
* Large photos are scaled down before processing for faster results
* Live log and progress bar
* Threaded background processing (no UI freezing)
* AI model preloads at startup, so processing starts immediately
* Auto-opens the output folder when finished (optional toggle)
* Clear error popup and log file if background removal can't load
* Modern dark UI

## Tech Stack

* Python 3
* CustomTkinter (GUI)
* tkinterdnd2 (drag & drop)
* Pillow (image processing)
* rembg + ONNX Runtime (AI background removal)
* PyInstaller (Windows build)

## Prerequisites

* Windows 10/11
* Python 3.10+ (tested on 3.13)
* [Microsoft Visual C++ Redistributable (x64)](https://aka.ms/vs/17/release/vc_redist.x64.exe), required by ONNX Runtime

## Installation & Build

1. Clone or download this repository.
2. Install dependencies via pip:

```
pip install customtkinter tkinterdnd2 pillow "rembg[cpu]"
```

3. Run from source:

```
python image_resizer_app.py
```

> When running from source, the first launch downloads the AI model (~170 MB) to `%LOCALAPPDATA%\PhotoResizerPro\models`. This only happens once.

### Build the Windows app

Place `build.bat`, `fix_vc_runtime.ps1`, `image_resizer_app.py` and `icon.ico` in the same folder, then double-click **`build.bat`**. It will:

1. Install all requirements (including PyInstaller)
2. Verify every package imports correctly
3. Download the AI model and bundle it into the app (so the app never downloads anything)
4. Build a fast-starting folder app with PyInstaller
5. Update the Microsoft C++ runtime DLLs inside the build (needed by ONNX Runtime)
6. Run a self-test to confirm background removal works in the built app

The finished app is at:

```
dist\PhotoResizerPro\PhotoResizerPro.exe
```

Keep the whole `PhotoResizerPro` folder together. To create a desktop shortcut, right-click the `.exe` → **Send to** → **Desktop (create shortcut)**.

## How to Use

1. Open PhotoResizer Pro and wait a few seconds for **"AI engine ready."** in the log.
2. Drag & drop your product photos onto the drop zone, or click **Browse Files**.
3. Click **Start Processing**.
4. Your images are saved in a `Resized_Output` folder next to the original photos, and the folder opens automatically when done.

Use **Clear Queue** to remove all selected photos before starting a new batch.

## Output Format

For each input photo named like `CODE-anything.ext` or `CODE_anything.ext`:

| File | Size | Background | Notes |
| --- | --- | --- | --- |
| `CODE.jpg` | Fits within 330 × 560 px | White | Aspect ratio preserved, sharpened, JPEG quality 92 |
| `CODE - copy.jpg` | Square, up to 1200 × 1200 px | White | Largest size that stays under 30 KB |

## Project Structure

```
PhotoResizer/
├── image_resizer_app.py   # Main application
├── build.bat              # One-click Windows build
├── fix_vc_runtime.ps1     # Fixes C++ runtime DLLs in the build
├── icon.ico               # App icon
└── models/                # AI model (created by build.bat)
```

## Troubleshooting

* **"Background removal unavailable" popup**: the full error is saved to `%LOCALAPPDATA%\PhotoResizerPro\error_log.txt`.
* **"DLL initialization routine failed"**: install the latest [Visual C++ Redistributable (x64)](https://aka.ms/vs/17/release/vc_redist.x64.exe), restart your PC, and run `build.bat` again.
* **Test a built app manually**: run `PhotoResizerPro.exe --selftest`. The result is written to `selftest_result.txt` next to the `.exe`.
* **Photos are resized but not cropped**: background removal failed to load; check the log box or `error_log.txt`.
