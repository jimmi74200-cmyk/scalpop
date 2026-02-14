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

def filter_data_for_indices(df, symbols=["NIFTY", "BANKNIFTY", "FINNIFTY"]):
    """
    Filters the DataFrame for specific indices and formats columns.
    """
    # Mapping for Kotak CSV usually:
    # pSymbol -> Symbol
    # pExpiryDate -> Expiry
    # pOptionType -> Option Type
    # dStrikePrice -> Strike Price
    # lToken -> Instrument Token

    col_map = {
        'pSymbol': 'symbol',
        'pExpiryDate': 'expiry',
        'pOptionType': 'option_type',
        'dStrikePrice': 'strike',
        'pTrdSymbol': 'trading_symbol',
        'lToken': 'instrument_token',
        # Adding potential alternates
        'Symbol': 'symbol',
        'Expiry': 'expiry',
        'OptionType': 'option_type',
        'StrikePrice': 'strike',
        'Token': 'instrument_token',
        'TradingSymbol': 'trading_symbol',
        # lowercase
        'psymbol': 'symbol',
        'pexpirydate': 'expiry',
        'poptiontype': 'option_type',
        'dstrikeprice': 'strike',
        'ltoken': 'instrument_token',
        'ptrdsymbol': 'trading_symbol'
    }

    # Rename known columns
    df = df.rename(columns=col_map)

    # Filter for symbols if 'symbol' column exists
    if 'symbol' in df.columns:
        df = df[df['symbol'].isin(symbols)]

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
