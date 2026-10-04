# Replaces outdated Microsoft C++ runtime DLLs that PyInstaller copied into the
# build with the newer ones from Windows\System32. onnxruntime needs 14.40+.
param([string]$Dist = "dist\PhotoResizerPro")

$ErrorActionPreference = "Stop"
$sys32 = Join-Path $env:SystemRoot "System32"
$internal = Join-Path $Dist "_internal"
if (-not (Test-Path $internal)) { $internal = $Dist }

$names = @("msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
           "msvcp140_atomic_wait.dll", "msvcp140_codecvt_ids.dll",
           "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll")

function Get-Ver($path) {
    $v = (Get-Item $path).VersionInfo
    return [version]("{0}.{1}.{2}.{3}" -f $v.FileMajorPart, $v.FileMinorPart, $v.FileBuildPart, $v.FilePrivatePart)
}

$sysMsvcp = Join-Path $sys32 "msvcp140.dll"
if (-not (Test-Path $sysMsvcp)) {
    Write-Host "WARNING: msvcp140.dll not found in System32. Install the Visual C++ Redistributable."
    exit 2
}
$sysVer = Get-Ver $sysMsvcp
Write-Host "System32 msvcp140.dll version: $sysVer"
if ($sysVer -lt [version]"14.40.0.0") {
    Write-Host "WARNING: Your Visual C++ runtime is older than 14.40 (onnxruntime needs 14.40+)."
    Write-Host "Install: https://aka.ms/vs/17/release/vc_redist.x64.exe  then run build.bat again."
}

# 1) Upgrade every stale copy anywhere in the build
foreach ($name in $names) {
    $src = Join-Path $sys32 $name
    if (-not (Test-Path $src)) { continue }
    $srcVer = Get-Ver $src
    Get-ChildItem -Path $Dist -Recurse -Filter $name -File | ForEach-Object {
        $curVer = Get-Ver $_.FullName
        if ($srcVer -gt $curVer) {
            Copy-Item $src $_.FullName -Force
            Write-Host "Updated $($_.FullName): $curVer -> $srcVer"
        } else {
            Write-Host "OK      $($_.FullName): $curVer"
        }
    }
    # 2) Make sure the app carries its own copy (so it works on other PCs too)
    $dst = Join-Path $internal $name
    if (-not (Test-Path $dst)) {
        Copy-Item $src $dst -Force
        Write-Host "Added   $dst ($srcVer)"
    }
}
exit 0