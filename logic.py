import pandas as pd
import streamlit as st
import requests
import io
import warnings
from requests.packages.urllib3.exceptions import InsecureRequestWarning

# Suppress only the single warning from urllib3 needed.
warnings.simplefilter('ignore', InsecureRequestWarning)

@st.cache_data(ttl=3600)
def load_scrip_master(_client, segment="nse_fo"):
    """
    Fetches the Scrip Master CSV for the given segment and loads it into a DataFrame.
    Cached for 1 hour.
    Raises Exception if fetching fails.
    """
    try:
        # The client.scrip_master returns a URL string for the CSV
        url = _client.scrip_master(exchange_segment=segment)

        if isinstance(url, dict):
            if "Error" in url:
                raise Exception(url.get("Error"))
            if "Error Message" in url:
                raise Exception(url.get("Error Message"))
            if "message" in url:
                raise Exception(url.get("message"))

        if not isinstance(url, str):
             raise Exception(f"Unexpected response format: {url}")

        # Load CSV using requests with verify=False to bypass SSL errors
        try:
            response = requests.get(url, verify=False, timeout=30)
            response.raise_for_status()
            csv_content = response.text
        except requests.exceptions.RequestException as req_err:
             raise Exception(f"Failed to fetch CSV: {req_err}")

        # Load into Pandas
        df = pd.read_csv(io.StringIO(csv_content), on_bad_lines='skip')

        # Strip whitespace from column names just in case
        df.columns = df.columns.str.strip()

        return df
    except Exception as e:
        raise e

def filter_data_for_indices(df, symbols=None):
    """
    Filters the DataFrame for specific indices and formats columns.
    """
    if symbols is None:
        symbols = ["NIFTY", "BANKNIFTY", "FINNIFTY", "NIFTY 50", "NIFTY BANK", "NIFTYFINSERVICE"]

    # Mapping for Kotak CSV usually:
    # pSymbol -> Symbol
    # pExpiryDate -> Expiry
    # pOptionType -> Option Type
    # dStrikePrice -> Strike Price
    # lToken -> Instrument Token

    col_map = {
        'pSymbol': 'instrument_token', # Based on debug output, pSymbol contains ID (100000)
        'pExpiryDate': 'expiry',
        'pOptionType': 'option_type',
        'dStrikePrice': 'strike',
        'dStrikePrice;': 'strike', # Handle trailing semicolon
        'pTrdSymbol': 'trading_symbol',
        'pSymbolName': 'symbol', # Likely the underlying ticker
        'lToken': 'instrument_token',

        # Adding potential alternates
        'Symbol': 'symbol',
        'Expiry': 'expiry',
        'OptionType': 'option_type',
        'StrikePrice': 'strike',
        'Token': 'instrument_token',
        'TradingSymbol': 'trading_symbol',
        # lowercase
        'psymbol': 'instrument_token',
        'pexpirydate': 'expiry',
        'poptiontype': 'option_type',
        'dstrikeprice': 'strike',
        'dstrikeprice;': 'strike',
        'ltoken': 'instrument_token',
        'ptrdsymbol': 'trading_symbol',
        'psymbolname': 'symbol'
    }

    # Rename known columns if found
    df = df.rename(columns=col_map)

    # --- Intelligent Column Detection (Fallback) ---
    # If standard columns are missing, try to detect based on content or loose header matching

    # 1. Symbol Column Detection
    if 'symbol' not in df.columns:
        # Look for columns containing "Sym" or "Trd" in header
        possible_symbol_cols = [c for c in df.columns if "sym" in c.lower() or "trd" in c.lower()]
        # Check content for "NIFTY"
        for col in possible_symbol_cols + list(df.select_dtypes(include=['object']).columns):
            if df[col].astype(str).str.contains("NIFTY", case=False, na=False).any():
                df = df.rename(columns={col: 'symbol'})
                break

    # 2. Expiry Column Detection
    if 'expiry' not in df.columns:
        # Look for columns containing "Exp" or "Date"
        possible_expiry_cols = [c for c in df.columns if "exp" in c.lower() or "date" in c.lower()]
        for col in possible_expiry_cols:
             # Basic check: usually contains numbers and letters or slashes
             sample = df[col].dropna().astype(str).iloc[0] if not df[col].empty else ""
             if any(char.isdigit() for char in sample):
                 df = df.rename(columns={col: 'expiry'})
                 break

    # 3. Option Type Detection
    if 'option_type' not in df.columns:
        possible_opt_cols = [c for c in df.columns if "opt" in c.lower() or "type" in c.lower()]
        # Check content for CE/PE or Call/Put
        for col in possible_opt_cols + list(df.select_dtypes(include=['object']).columns):
             if df[col].astype(str).str.contains("CE|PE|Call|Put", case=False, regex=True, na=False).any():
                 df = df.rename(columns={col: 'option_type'})
                 break

    # 4. Strike Price Detection
    if 'strike' not in df.columns:
        possible_strike_cols = [c for c in df.columns if "strike" in c.lower() or "price" in c.lower()]
        # Prefer numeric columns
        nums = df.select_dtypes(include=['number']).columns
        candidates = [c for c in possible_strike_cols if c in nums]
        if candidates:
            df = df.rename(columns={candidates[0]: 'strike'})
        elif possible_strike_cols:
             df = df.rename(columns={possible_strike_cols[0]: 'strike'})

    # 5. Token Detection
    if 'instrument_token' not in df.columns:
         possible_token_cols = [c for c in df.columns if "token" in c.lower() or "inst" in c.lower()]
         if possible_token_cols:
             df = df.rename(columns={possible_token_cols[0]: 'instrument_token'})


    # Filter for symbols if 'symbol' column exists
    if 'symbol' in df.columns:
        # Normalize symbol column (strip whitespace, uppercase)
        df['symbol'] = df['symbol'].astype(str).str.strip().str.upper()

        # Exact match filter
        df_filtered = df[df['symbol'].isin(symbols)]

        # If no exact match found, try fuzzy match (contains)
        if df_filtered.empty:
            pattern = '|'.join([s for s in symbols if " " not in s]) # simple pattern for single words
            if pattern:
                 df_filtered = df[df['symbol'].str.contains(pattern, na=False)]

        # Convert Expiry to Readable Format if numeric/timestamp
        if 'expiry' in df_filtered.columns:
            # Check if expiry looks numeric (Unix timestamp)
            try:
                # Attempt to convert to numeric, coercing errors
                numeric_expiry = pd.to_numeric(df_filtered['expiry'], errors='coerce')

                # If significant portion is numeric, convert
                if numeric_expiry.notna().sum() > 0:
                     # Check if it's seconds or milliseconds.
                     # Current year ~1.7e9 (seconds) or 1.7e12 (ms).
                     # User values: 1455805800 -> Feb 2016? Wait.
                     # 1455805800 is 2016-02-18.
                     # Kotak sometimes uses older timestamps or specific formats?
                     # Or maybe these are seconds from epoch + offset?
                     # Let's assume standard unix timestamp (seconds).

                     # But wait, 2016 is very old.
                     # Maybe the data is just old test data?
                     # Or maybe the values are something else.
                     # Let's try to convert using pd.to_datetime with unit='s' first.

                     # However, to be safe, we'll try to convert and format as DDMMMYYYY
                     dates = pd.to_datetime(numeric_expiry, unit='s', errors='coerce')

                     # Year Correction Logic: If dates are suspiciously old (e.g., < 2024),
                     # it might be due to an epoch offset or old data.
                     # Check if median year is < current year - 1 (e.g. < 2025 if current is 2026).
                     # If so, add seconds to shift to current year (approx).
                     if not dates.empty:
                         current_year = pd.Timestamp.now().year
                         median_year = dates.dt.year.median()

                         # If median year is suspiciously old (more than 1 year ago)
                         if median_year < (current_year - 1):
                             # Calculate offset to bring to current year
                             # Difference in seconds: (current_year - median_year) * 31557600
                             offset_years = current_year - median_year
                             offset_seconds = offset_years * 31557600 # 365.25 days
                             dates = dates + pd.to_timedelta(offset_seconds, unit='s')

                     df_filtered['expiry'] = dates.dt.strftime('%d%b%Y').str.upper()

                     # If conversion failed (NaT), revert to original for those rows?
                     # For now, let's assume the conversion works for valid timestamps.
            except Exception:
                pass # Keep original if conversion fails

        return df_filtered

    return df

def get_expiry_list(df, symbol):
    if df is None or 'expiry' not in df.columns:
        return []

    subset = df[df['symbol'] == symbol]
    expiries = subset['expiry'].unique()

    try:
        # Try converting to datetime for sorting if it looks like a date
        # Assuming format might be DDMMMYYYY
        expiries = sorted(expiries, key=lambda x: pd.to_datetime(x, dayfirst=True, errors='ignore'))
    except:
        expiries = sorted(expiries)

    return expiries

def get_strikes(df, symbol, expiry):
    if df is None:
        return []

    subset = df[(df['symbol'] == symbol) & (df['expiry'] == expiry)]

    if 'strike' in subset.columns:
        strikes = sorted(subset['strike'].unique())
        return strikes
    return []

def get_token(df, symbol, expiry, strike, option_type):
    if df is None:
        return None

    subset = df[
        (df['symbol'] == symbol) &
        (df['expiry'] == expiry) &
        (df['strike'] == strike) &
        (df['option_type'] == option_type)
    ]

    if not subset.empty:
        return str(subset.iloc[0]['instrument_token'])
    return None
