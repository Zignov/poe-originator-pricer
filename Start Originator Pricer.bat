@echo off
rem Double-click launcher for the PoE Originator Map Stash Pricer.
rem
rem It is deliberately not a frozen .exe. The app is a Streamlit server plus
rem CatBoost/LightGBM/XGBoost, which PyInstaller bundles to ~800 MB and breaks
rem on every dependency bump. Instead this builds a normal virtual environment
rem beside itself on first run and reuses it after that, so the second start is
rem instant and `pip install` remains the thing that fixes a broken install.
rem
rem Everything it creates -- .venv, config.yaml, .env, data/originator.db --
rem stays inside this folder. Deleting the folder uninstalls it.

title Originator Pricer
cd /d "%~dp0"

set "VENV=.venv"
set "VPY=%VENV%\Scripts\python.exe"
rem Forward slashes: this path is pasted into a Python string literal below,
rem where a backslash would be read as an escape.
set "STAMP=.venv/.deps-stamp"

echo.
echo   PoE Originator Map Stash Pricer
echo   ===============================
echo.

if exist "%VPY%" goto :have_venv

call :find_python
if errorlevel 1 goto :no_python

echo   Creating a private Python environment in %VENV% ...
%PY_CMD% %PY_ARG% -m venv "%VENV%"
if not exist "%VPY%" goto :venv_failed
echo.

:have_venv

rem Reinstall only when requirements.txt has actually changed since last time.
"%VPY%" -c "import hashlib,pathlib,sys;s=pathlib.Path('%STAMP%');r=pathlib.Path('requirements.txt');sys.exit(0 if s.exists() and s.read_text().strip()==hashlib.sha256(r.read_bytes()).hexdigest() else 1)" 2>nul
if not errorlevel 1 goto :have_deps

echo   Installing dependencies.
echo   The first run downloads a few hundred MB and can take several minutes.
echo   Later runs skip this entirely.
echo.
"%VPY%" -m pip install --upgrade pip --quiet --disable-pip-version-check
"%VPY%" -m pip install -r requirements.txt --disable-pip-version-check
if errorlevel 1 goto :pip_failed
"%VPY%" -c "import hashlib,pathlib;pathlib.Path('%STAMP%').write_text(hashlib.sha256(pathlib.Path('requirements.txt').read_bytes()).hexdigest())"
echo.

:have_deps

"%VPY%" scripts\launch.py
if errorlevel 1 goto :app_failed
exit /b 0


rem ---------------------------------------------------------------------
rem Prefer the py launcher, which is what a python.org install registers, and
rem fall back to whatever `python` is on PATH. Either must be 3.11 or newer.
:find_python
py -3 -c "import sys;raise SystemExit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1
if not errorlevel 1 (set "PY_CMD=py" & set "PY_ARG=-3" & exit /b 0)
python -c "import sys;raise SystemExit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1
if not errorlevel 1 (set "PY_CMD=python" & set "PY_ARG=" & exit /b 0)
exit /b 1

:no_python
echo   No Python 3.11 or newer was found.
echo.
echo   Install it from https://www.python.org/downloads/windows/
echo   and tick "Add python.exe to PATH" in the installer, then run this again.
echo.
pause
exit /b 1

:venv_failed
echo.
echo   Could not create the virtual environment in %VENV%.
echo   If this folder is inside OneDrive or Program Files, move it somewhere
echo   like C:\Games\OriginatorPricer and try again.
echo.
pause
exit /b 1

:pip_failed
echo.
echo   Installing the dependencies failed. The message above says why.
echo   The usual causes are no internet connection or a corporate proxy.
echo   To start over, delete the %VENV% folder and run this again.
echo.
pause
exit /b 1

:app_failed
echo.
echo   The app exited with an error. The message above says why.
echo.
pause
exit /b 1
