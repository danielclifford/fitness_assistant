@echo off
setlocal enabledelayedexpansion

echo ============================================================
echo   Fitness Assistant — First-time Setup
echo ============================================================
echo.

set "PROJECT_DIR=%~dp0"
set "VENV_DIR=%PROJECT_DIR%.venv"
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
set "CONFIG_DIR=%APPDATA%\Claude"
set "CONFIG_FILE=%CONFIG_DIR%\claude_desktop_config.json"

REM --- Create virtual environment ---
echo [1/4] Creating Python virtual environment...
python -m venv "%VENV_DIR%"
if errorlevel 1 (
    echo ERROR: Could not create virtual environment.
    echo Make sure Python 3.10+ is installed and on your PATH.
    pause & exit /b 1
)
echo       Done.

REM --- Install dependencies ---
echo.
echo [2/4] Installing dependencies (mcp, requests, python-dotenv)...
"%VENV_DIR%\Scripts\pip.exe" install -r "%PROJECT_DIR%requirements.txt" --quiet
if errorlevel 1 (
    echo ERROR: pip install failed. Check your internet connection.
    pause & exit /b 1
)
echo       Done.

REM --- Initialise database ---
echo.
echo [3/4] Initialising database...
"%PYTHON_EXE%" "%PROJECT_DIR%src\database.py"
if errorlevel 1 (
    echo ERROR: Database initialisation failed.
    pause & exit /b 1
)
echo       Done.

REM --- Write Claude Desktop MCP config ---
echo.
echo [4/4] Configuring Claude Desktop...

if not exist "%CONFIG_DIR%" mkdir "%CONFIG_DIR%"

REM Build the run_mcp path with forward slashes for JSON
set "RUN_MCP=%PROJECT_DIR%run_mcp.py"
set "RUN_MCP=%RUN_MCP:\=/%"
set "PYTHON_PATH=%PYTHON_EXE%"
set "PYTHON_PATH=%PYTHON_PATH:\=/%"

REM Check if config already exists and has content
if exist "%CONFIG_FILE%" (
    echo       Existing Claude Desktop config found.
    echo       Backing up to claude_desktop_config.json.bak
    copy "%CONFIG_FILE%" "%CONFIG_FILE%.bak" >nul
)

REM Write config — if file already exists with other MCP servers,
REM this will overwrite. Manual merge may be needed in that case.
(
echo {
echo   "mcpServers": {
echo     "fitness-assistant": {
echo       "command": "%PYTHON_PATH%",
echo       "args": ["%RUN_MCP%"]
echo     }
echo   }
echo }
) > "%CONFIG_FILE%"

echo       Claude Desktop config written to:
echo       %CONFIG_FILE%
echo.

echo ============================================================
echo   Setup complete!
echo ============================================================
echo.
echo Next steps:
echo.
echo   1. Authorise Strava (run once):
echo      Double-click  strava_auth.py
echo      or run:  "%PYTHON_EXE%" "%PROJECT_DIR%strava_auth.py"
echo.
echo   2. Import your data:
echo      Drop MacroFactor, Strong, or Apple Health files into:
echo        %PROJECT_DIR%data\imports\
echo      Then double-click  import_data.py
echo.
echo   3. Restart Claude Desktop (fully quit and reopen).
echo.
echo   4. In Claude Desktop, start a new conversation.
echo      You should see a hammer icon indicating MCP tools are active.
echo.
pause
