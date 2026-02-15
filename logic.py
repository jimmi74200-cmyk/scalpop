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
        symbols = [
            "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "MIDCAPNIFTY", "NIFTYNXT50",
            "NIFTY 50", "NIFTY BANK", "NIFTYFINSERVICE", "FINANCIAL SERVICES",
            "SENSEX", "BANKEX"
        ]

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
        'pExchSeg': 'exchange_segment', # Add segment mapping
        'pSegment': 'exchange_segment',
        'pInstType': 'instrument_type', # Add instrument type mapping

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
         possible_token_cols = [c for c in df.columns if "token" in c.lower() or "code" in c.lower()]
         if possible_token_cols:
             df = df.rename(columns={possible_token_cols[0]: 'instrument_token'})

    # 6. Instrument Type Detection
    if 'instrument_type' not in df.columns:
        possible_type_cols = [c for c in df.columns if "inst" in c.lower() or "type" in c.lower()]
        for col in possible_type_cols:
             if df[col].astype(str).str.contains("FUT|OPT|IDX|INDEX", case=False, regex=True, na=False).any():
                 df = df.rename(columns={col: 'instrument_type'})
                 break

    # 7. Exchange Segment Detection
    if 'exchange_segment' not in df.columns:
        possible_seg_cols = [c for c in df.columns if "seg" in c.lower() or "exch" in c.lower()]
        for col in possible_seg_cols:
             if df[col].astype(str).str.contains("nse|bse|mcx", case=False, regex=True, na=False).any():
                 df = df.rename(columns={col: 'exchange_segment'})
                 break

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
                     dates = pd.to_datetime(numeric_expiry, unit='s', errors='coerce')

                     # Year Correction Logic
                     if not dates.empty:
                         current_year = pd.Timestamp.now().year
                         median_year = dates.dt.year.median()

                         # If median year is suspiciously old (more than 1 year ago)
                         if median_year < (current_year - 1):
                             offset_years = current_year - median_year
                             offset_seconds = offset_years * 31557600 # 365.25 days
                             dates = dates + pd.to_timedelta(offset_seconds, unit='s')

                     df_filtered['expiry'] = dates.dt.strftime('%d%b%Y').str.upper()
            except Exception:
                pass # Keep original if conversion fails

        # Clean Instrument Tokens (Remove decimals if present)
        if 'instrument_token' in df_filtered.columns:
            # Convert to numeric first to handle string representations
            df_filtered['instrument_token'] = pd.to_numeric(df_filtered['instrument_token'], errors='coerce').fillna(0).astype(int).astype(str)

        # Clean Strike Prices (Convert to float for consistency)
        if 'strike' in df_filtered.columns:
             # Handle weird formats like "18000;"
             df_filtered['strike'] = df_filtered['strike'].astype(str).str.replace(';', '', regex=False)
             df_filtered['strike'] = pd.to_numeric(df_filtered['strike'], errors='coerce')

             # Heuristic: If strikes are excessively large (e.g., > 200,000 for NIFTY/BANKNIFTY),
             # they are likely in paisa (multiplied by 100).
             # Check median strike price.
             if not df_filtered['strike'].empty:
                 median_strike = df_filtered['strike'].median()
                 if median_strike > 200000:
                     df_filtered['strike'] = df_filtered['strike'] / 100.0

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

def get_token_and_segment(df, symbol, expiry, strike, option_type):
    if df is None:
        return None, None

    subset = df[
        (df['symbol'] == symbol) &
        (df['expiry'] == expiry) &
        (df['strike'] == strike) &
        (df['option_type'] == option_type)
    ]

    if not subset.empty:
        token = str(subset.iloc[0]['instrument_token']).strip()
        # Try to get segment if available
        segment = "nse_fo" # default
        if 'exchange_segment' in subset.columns:
            seg_val = str(subset.iloc[0]['exchange_segment']).lower().strip()
            # Validate segment against known allowed values
            allowed_segments = ['nse_cm', 'nse_fo', 'bse_cm', 'bse_fo', 'cde_fo', 'mcx_fo']
            if seg_val in allowed_segments:
                segment = seg_val
            elif "nse" in seg_val and "fo" in seg_val:
                segment = "nse_fo"
            elif "bse" in seg_val and "fo" in seg_val:
                segment = "bse_fo"
            # Add more heuristics if needed, otherwise fallback to nse_fo

        return token, segment
    return None, None
