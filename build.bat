@echo off
setlocal
cd /d "%~dp0"

echo.
echo Using this Python:
python -c "import sys; print(sys.executable, sys.version)"
if errorlevel 1 goto :fail

echo.
echo [1/6] Installing requirements...
python -m pip install --upgrade pip
python -m pip install --upgrade customtkinter tkinterdnd2 pillow "rembg[cpu]" pyinstaller pyinstaller-hooks-contrib
if errorlevel 1 goto :fail

echo.
echo [2/6] Checking that everything imports...
python -c "import os; os.environ['NUMBA_DISABLE_JIT']='1'; import onnxruntime; from rembg import remove, new_session; import customtkinter, tkinterdnd2, PyInstaller; print('All packages OK, onnxruntime', onnxruntime.__version__)"
if errorlevel 1 (
    echo.
    echo A package did not install into the Python shown above.
    echo Scroll up to the pip output in step 1 for the real error.
    goto :fail
)

echo.
echo [3/6] Getting AI model (one time, ~170 MB) so the app never downloads it...
if not exist models mkdir models
if not exist models\u2net.onnx (
    powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri 'https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx' -OutFile 'models\u2net.onnx'"
    if errorlevel 1 goto :fail
)

echo.
echo [4/6] Building app (folder build = fast startup)...
if exist PhotoResizerPro.spec del PhotoResizerPro.spec
if exist dist\PhotoResizerPro rmdir /s /q dist\PhotoResizerPro
set ICON_ARGS=
if exist icon.ico set ICON_ARGS=--icon icon.ico --add-data "icon.ico;."

python -m PyInstaller --noconfirm --clean --onedir --windowed ^
  --name "PhotoResizerPro" %ICON_ARGS% ^
  --add-data "models;models" ^
  --collect-all customtkinter ^
  --collect-all tkinterdnd2 ^
  --collect-all rembg ^
  --collect-all onnxruntime ^
  --collect-all skimage ^
  --collect-all lazy_loader ^
  --collect-all pymatting ^
  --collect-all pooch ^
  --collect-all jsonschema ^
  --collect-all jsonschema_specifications ^
  --collect-submodules scipy.ndimage ^
  --copy-metadata rembg --copy-metadata pymatting --copy-metadata pooch --copy-metadata numba ^
  image_resizer_app.py
if errorlevel 1 goto :fail

echo.
echo [5/6] Updating Microsoft C++ runtime DLLs inside the app (needed by onnxruntime)...
if not exist "%~dp0fix_vc_runtime.ps1" (
    echo fix_vc_runtime.ps1 is missing - put it in the same folder as build.bat.
    goto :fail
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0fix_vc_runtime.ps1" -Dist "dist\PhotoResizerPro"
if errorlevel 2 goto :selftest_fail

echo.
echo [6/6] Self-test: checking the AI loads inside the built app...
set EXE=dist\PhotoResizerPro\PhotoResizerPro.exe
set RESULT=dist\PhotoResizerPro\selftest_result.txt
if exist "%RESULT%" del "%RESULT%"
start "" /wait "%EXE%" --selftest
if exist "%RESULT%" type "%RESULT%"
findstr /c:"SELFTEST OK" "%RESULT%" >nul 2>&1
if errorlevel 1 goto :selftest_fail

del "%RESULT%" >nul 2>&1
echo.
echo ==========================================================
echo  Done! AI background removal works in the built app.
echo  Your app:  dist\PhotoResizerPro\PhotoResizerPro.exe
echo  Keep the whole "PhotoResizerPro" folder together.
echo  Right-click the .exe ^> Send to ^> Desktop (create shortcut).
echo ==========================================================
pause
exit /b 0

:selftest_fail
echo.
echo The app built, but the AI still fails to load inside it.
echo 1) Install the latest Microsoft Visual C++ Redistributable (x64), then RESTART your PC:
echo    https://aka.ms/vs/17/release/vc_redist.x64.exe
echo 2) Run build.bat again.
echo 3) If it still fails, send everything printed in steps 5 and 6.
pause
exit /b 1

:fail
echo.
echo BUILD FAILED - scroll up to see the error.
pause
exit /b 1