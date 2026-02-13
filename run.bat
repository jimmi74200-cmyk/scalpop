@echo off
REM Check for Python
python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo Python is not installed or not in PATH. Please install Python 3.10+ from python.org.
    pause
    exit /b
)

REM Check for Git (required for neo_api_client)
git --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo Git is not installed or not in PATH. Please install Git from git-scm.com.
    echo The Kotak Neo API client requires git to install.
    pause
    exit /b
)

REM Install Dependencies
echo Installing dependencies...
pip install -r requirements.txt
IF %ERRORLEVEL% NEQ 0 (
    echo Failed to install dependencies. Check your internet connection and git installation.
    pause
    exit /b
)

REM Run Dashboard
echo Starting Dashboard...
streamlit run dashboard.py
pause
