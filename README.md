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

- **Quick Login**: Secure, session-based login (v2 API flow).
- **Option Chain**: View NIFTY/BANKNIFTY/FINNIFTY option chains with live LTP.
- **Auto-Strike Selection**: ATM/ITM/OTM selection with "Refresh" logic based on Future/Spot price.
- **Robust Data Handling**: Fixes for Scrip Master column names, expiry dates (2016->Current), and strike scaling.
- **Order Placement**:
  - Entry: Market Order.
  - Stop Loss: Separate **Stop Loss Limit (SL)** order placed immediately after entry success.
- **Debugging**: Built-in "Quote Tester" and Raw Response viewer for troubleshooting.

---
*Last Updated: 2025-02-27 (Fixed Spot Index Fallback & SL Limit Order)*
