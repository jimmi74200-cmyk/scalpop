# Kotak Neo Quick Dashboard

A Streamlit-based dashboard for quick options trading using the Kotak Neo API v2.

## Prerequisites

1. **Python 3.10+**: Ensure Python is installed and added to your PATH.
2. **Git**: The Kotak Neo API client requires Git to be installed. Download from [git-scm.com](https://git-scm.com/).

## Installation

### Windows (Easy)
Double-click `run.bat` to automatically install dependencies and start the dashboard.

### Manual Installation (All OS)

1. Open a terminal/command prompt.
2. Navigate to the project folder.
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
   **Note:** If `pip` is not recognized, try `pip3` or `python -m pip`.

## Usage

Start the dashboard:
```bash
streamlit run dashboard.py
```
Or use the `run.bat` / `run.sh` scripts.

## Features

- **Quick Login**: Save your credentials securely (session-based).
- **Option Chain**: View NIFTY/BANKNIFTY/FINNIFTY option chains with live LTP.
- **Order Placement**: Place Market Entry orders.
- **Stop Loss**: Automatically places a secondary Stop Loss Market (SL-M) order based on your SL points input.
