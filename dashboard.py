import streamlit as st
import pandas as pd
import sys

# Try to import NeoAPI
try:
    from neo_api_client import NeoAPI
except ImportError:
    st.error("Neo API Client not installed!")
    st.warning("Please install the dependencies first.")
    st.code("pip install -r requirements.txt", language="bash")
    st.stop()
    sys.exit(1)

from logic import load_scrip_master, filter_data_for_indices, get_expiry_list, get_strikes, get_token_and_segment

# Page configuration
st.set_page_config(page_title="Kotak Neo Quick Dashboard", layout="wide")

# Sidebar for Authentication
with st.sidebar:
    st.header("Authentication")

    # Test Quote Debug Tool
    if st.checkbox("Show Quote Tester"):
        st.subheader("Quote Tester")

        # Helper to get values from session state if set by main UI
        def_token = st.session_state.get('debug_token', '')
        def_seg = st.session_state.get('debug_seg', 'nse_fo')

        t_token = st.text_input("Instrument Token", value=def_token, help="Enter the numeric token ID (e.g., 10000). You can auto-fill this from the main dashboard.")
        t_seg = st.text_input("Exchange Segment", value=def_seg, help="e.g., nse_fo, bse_fo, nse_cm")

        if st.button("Get Quote"):
            if 'client' in st.session_state:
                try:
                    # Clean token input
                    clean_token = str(t_token).strip()
                    res = st.session_state['client'].quotes(instrument_tokens=[{"instrument_token": clean_token, "exchange_segment": t_seg}], quote_type="ltp")
                    st.write(res)
                except Exception as e:
                    st.error(f"Error: {e}")
            else:
                st.error("Please login first")
        st.markdown("---")

    consumer_key = st.text_input("Consumer Key", type="password", help="From Kotak Neo Trade API settings")
    mobile_number = st.text_input("Mobile Number", help="Registered Mobile Number with Country Code (e.g., +91...)")
    # password = st.text_input("Password", type="password", help="Your account password") # Not used in TOTP flow
    ucc = st.text_input("UCC (User Client Code)")
    mpin = st.text_input("MPIN", type="password")
    totp = st.text_input("Current TOTP", help="Time-based One-Time Password from your authenticator app")

    if st.button("Login"):
        if not (consumer_key and mobile_number and mpin and totp and ucc):
            st.error("Please fill all fields")
        else:
            try:
                # Initialize Client
                client = NeoAPI(consumer_key=consumer_key, environment='prod')

                # Step 1: Login with Mobile/UCC/TOTP
                login_resp = client.totp_login(mobile_number=mobile_number, ucc=ucc, totp=totp)
                if not login_resp or 'data' not in login_resp or 'token' not in login_resp['data']:
                    st.error(f"Login Failed: {login_resp.get('error', login_resp)}")
                else:
                    # Step 2: Validate MPIN
                    validate_resp = client.totp_validate(mpin=mpin)
                    if not validate_resp or 'data' not in validate_resp or 'token' not in validate_resp['data']:
                        st.error(f"MPIN Validation Failed: {validate_resp.get('error', validate_resp)}")
                    else:
                        # Success
                        st.session_state['client'] = client
                        st.session_state['ucc'] = ucc
                        st.success("Logged in successfully!")
                        st.rerun()
            except Exception as e:
                st.error(f"Login process failed: {e}")

# Main Content
st.title("Kotak Neo Quick Options Dashboard")

if 'client' in st.session_state:
    client = st.session_state['client']
    st.success(f"Connected as {st.session_state.get('ucc', 'Unknown')}")

    if st.button("Logout"):
        del st.session_state['client']
        st.rerun()

    # Fetch Scrip Master (F&O and Cash for Spot Indices)
    df_master = None
    try:
        with st.spinner("Loading Scrip Master (F&O + Cash)..."):
            # Load F&O
            df_fo = load_scrip_master(client, "nse_fo")

            # Load Cash (for Spot Indices)
            # Use a separate try-except block for CM to avoid blocking FO if CM fails
            df_cm = None
            try:
                df_cm = load_scrip_master(client, "nse_cm")
            except Exception as cm_e:
                st.warning(f"Could not load Cash Market data (Spot Indices might be unavailable): {cm_e}")

            # Combine
            if df_fo is not None and df_cm is not None:
                df_master = pd.concat([df_fo, df_cm], ignore_index=True)
            elif df_fo is not None:
                df_master = df_fo
            elif df_cm is not None:
                df_master = df_cm

    except Exception as e:
        st.error(f"Error loading Scrip Master: {e}")

    if df_master is not None:
        # Debug Mode Toggle
        if st.sidebar.checkbox("Show Raw Scrip Master Columns", value=True):
            st.write("Raw Columns:", df_master.columns.tolist())
            st.dataframe(df_master.head())

        # Process Data using logic.py (handles robust renaming and filtering)
        try:
            df_indices = filter_data_for_indices(df_master)

            if df_indices.empty and not df_master.empty:
                 st.warning("No indices found after filtering. Check if Scrip Master format has changed.")
                 # Show helpful debug info
                 st.write("Available Symbols (First 50):", sorted(df_master.iloc[:, 0].astype(str).unique())[:50])
                 st.write("Detected Columns:", df_master.columns.tolist())

            # UI Layout
            col1, col2 = st.columns(2)

            with col1:
                # Symbol Selection
                available_symbols = sorted(df_indices['symbol'].unique()) if 'symbol' in df_indices.columns else []
                symbol = st.selectbox("Symbol", available_symbols, index=0 if available_symbols else None)

            with col2:
                # Expiry Selection
                expiries = get_expiry_list(df_indices, symbol)
                expiry = st.selectbox("Expiry", expiries, index=0 if expiries else None)

            # Strikes Row
            if symbol and expiry:
                # Get Strikes
                strikes = get_strikes(df_indices, symbol, expiry)

                # Strike Selection Controls
                st.markdown("##### Strike Selection")
                sc1, sc2, sc3 = st.columns([1, 1, 2])
                with sc1:
                    strike_opts = ["ATM", "ITM1", "ITM2", "ITM3", "ITM4", "ITM5", "OTM1", "OTM2", "OTM3", "OTM4", "OTM5"]
                    strike_mode = st.selectbox("Select Strike", strike_opts, key="strike_mode")
                with sc2:
                    if st.button("Refresh Strikes"):
                        # Logic to find Future Price and auto-select strikes
                        try:
                            # 1. Find Future Token
                            # Strategy:
                            # A. Exact Match: Symbol + Expiry + FUT
                            # B. Symbol Match: Symbol + Nearest Expiry + FUT
                            # C. Root Symbol Match: Root(Symbol) + Nearest Expiry + FUT (e.g. NIFTY vs NIFTY 50)

                            root_symbol = symbol.split()[0] # e.g. "NIFTY" from "NIFTY 50" or "NIFTY"

                            # Filter for ANY Future matching the root symbol
                            # Debug: check available types
                            # st.write("Types:", df_indices['instrument_type'].unique())

                            all_futs = df_indices[
                                (df_indices['symbol'].str.contains(root_symbol, case=False, na=False)) &
                                (df_indices['instrument_type'].astype(str).str.contains("FUT", case=False, na=False))
                            ].copy()

                            fut_subset = pd.DataFrame()

                            if not all_futs.empty:
                                # Try exact expiry first
                                exact_futs = all_futs[all_futs['expiry'] == expiry]
                                if not exact_futs.empty:
                                    fut_subset = exact_futs.head(1)
                                else:
                                    # Find nearest future expiring >= option expiry
                                    all_futs['expiry_dt'] = pd.to_datetime(all_futs['expiry'], format='%d%b%Y', errors='coerce')
                                    curr_opt_expiry = pd.to_datetime(expiry, format='%d%b%Y', errors='coerce')

                                    if pd.notna(curr_opt_expiry):
                                        future_futs = all_futs[all_futs['expiry_dt'] >= curr_opt_expiry].sort_values('expiry_dt')
                                        if not future_futs.empty:
                                            fut_subset = future_futs.head(1)

                            # Fallback: Try finding Spot Index if Future not found
                            if fut_subset.empty:
                                # Look for Index in NSE_CM
                                # Filter: Symbol matches root, Type contains INDEX/IDX
                                # Note: Index tokens are usually in a different segment (nse_cm) and might not have expiry.
                                # But df_indices is filtered for indices.

                                idx_subset = df_indices[
                                    (df_indices['symbol'].str.contains(root_symbol, case=False, na=False)) &
                                    (df_indices['instrument_type'].astype(str).str.contains("INDEX|IDX", case=False, regex=True, na=False))
                                ].copy()

                                if not idx_subset.empty:
                                    # Prefer NSE_CM
                                    # Check segments if available
                                    if 'exchange_segment' in idx_subset.columns:
                                        nse_idx = idx_subset[idx_subset['exchange_segment'].str.contains('cm', case=False, na=False)]
                                        if not nse_idx.empty:
                                            idx_subset = nse_idx

                                    fut_subset = idx_subset.head(1)
                                    st.info(f"Using Spot Index ({fut_subset.iloc[0]['symbol']}) as reference.")

                            # Debug info if still failed
                            if fut_subset.empty:
                                st.warning(f"No Future or Index found for {symbol} (Root: {root_symbol}).")
                                # Extended Debugging
                                col_matches = [c for c in df_indices.columns if 'sym' in c.lower()]
                                st.write("Root Symbol:", root_symbol)
                                st.write("Available Instrument Types:", df_indices['instrument_type'].unique())

                                # Show any rows partially matching root symbol that are NOT options
                                partials = df_indices[
                                    (df_indices['symbol'].str.contains(root_symbol, case=False, na=False)) &
                                    (~df_indices['instrument_type'].astype(str).str.contains("OPT", case=False, na=False))
                                ]
                                if not partials.empty:
                                    st.write("Potential Candidates (Non-Options):", partials[['symbol', 'instrument_type', 'exchange_segment']].head(10))
                                else:
                                    st.write("No non-option candidates found matching root.")

                            ref_price = None

                            if not fut_subset.empty:
                                fut_token = str(fut_subset.iloc[0]['instrument_token']).strip()
                                fut_seg = str(fut_subset.iloc[0]['exchange_segment']).strip().lower()
                                if not fut_seg: fut_seg = "nse_fo"
                                # If it's an index, seg might be nse_cm. Ensure we use the row's segment.

                                # Fetch LTP
                                q = client.quotes(instrument_tokens=[{"instrument_token": fut_token, "exchange_segment": fut_seg}], quote_type="ltp")

                                # Mini-extractor
                                def quick_extract(resp):
                                    if isinstance(resp, dict):
                                        for k,v in resp.items():
                                            if str(k).strip().lower() in ['ltp', 'last_price', 'close']: return v
                                            if isinstance(v, (dict, list)):
                                                r = quick_extract(v)
                                                if r: return r
                                    elif isinstance(resp, list):
                                        for i in resp:
                                            r = quick_extract(i)
                                            if r: return r
                                    return None

                                ref_price = quick_extract(q)

                            if ref_price:
                                ref_price = float(ref_price)
                                st.toast(f"Reference Price (Future): {ref_price}")

                                # Find Closest Strike (ATM)
                                # strikes is a list of floats, assumed sorted ascending
                                closest_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - ref_price))

                                # Parse Mode (ATM, ITM1, OTM2, etc.)
                                offset = 0
                                if "ITM" in strike_mode:
                                    offset = int(strike_mode.replace("ITM", ""))
                                    # CE ITM = Lower Strike (-offset)
                                    # PE ITM = Higher Strike (+offset)
                                    ce_offset = -offset
                                    pe_offset = offset
                                elif "OTM" in strike_mode:
                                    offset = int(strike_mode.replace("OTM", ""))
                                    # CE OTM = Higher Strike (+offset)
                                    # PE OTM = Lower Strike (-offset)
                                    ce_offset = offset
                                    pe_offset = -offset
                                else:
                                    # ATM
                                    ce_offset = 0
                                    pe_offset = 0

                                ce_idx = max(0, min(len(strikes)-1, closest_idx + ce_offset))
                                pe_idx = max(0, min(len(strikes)-1, closest_idx + pe_offset))

                                st.session_state['ce_idx_val'] = ce_idx
                                st.session_state['pe_idx_val'] = pe_idx
                                st.rerun()
                            else:
                                st.warning("Could not fetch Reference Price (Future) to auto-select.")

                        except Exception as e:
                            st.error(f"Auto-select failed: {e}")

                col3, col4, col5 = st.columns(3)

                def extract_ltp(response):
                    """Helper to extract LTP recursively from any nested structure"""
                    if not response:
                        return "No Resp"

                    # Recursive search function
                    def find_key(obj, target_key):
                        if isinstance(obj, dict):
                            # Case-insensitive check for the key in current dict
                            for k, v in obj.items():
                                if str(k).strip().lower() == target_key.lower():
                                    return v

                            # Recursive step
                            for v in obj.values():
                                result = find_key(v, target_key)
                                if result is not None:
                                    return result
                        elif isinstance(obj, list):
                            for item in obj:
                                result = find_key(item, target_key)
                                if result is not None:
                                    return result
                        return None

                    # Search for 'ltp' (will match ltp, LTP, Ltp, etc. due to logic above)
                    # Also check synonyms just in case
                    keys_to_check = ['ltp', 'last_price', 'last_traded_price', 'close', 'lp']
                    for k in keys_to_check:
                        val = find_key(response, k)
                        if val is not None:
                            return val

                    return "N/A"

                with col3:
                    # Determine default index
                    ce_def_idx = st.session_state.get('ce_idx_val', len(strikes)//2 if strikes else 0)
                    if ce_def_idx >= len(strikes): ce_def_idx = 0

                    ce_strike = st.selectbox("CE Strike", strikes, index=ce_def_idx, format_func=lambda x: f"{float(x):g}", key="ce_strike_box")
                    # Get LTP for CE
                    ce_token, ce_seg = get_token_and_segment(df_indices, symbol, expiry, ce_strike, "CE")
                    ce_ltp = "Loading..."

                    if ce_token:
                        try:
                            seg = ce_seg if ce_seg else "nse_fo"
                            q = client.quotes(instrument_tokens=[{"instrument_token": ce_token, "exchange_segment": seg}], quote_type="ltp")
                            ce_ltp = extract_ltp(q)

                            # Always allow viewing raw response
                            with st.expander("Raw CE Response"):
                                st.write(q)

                        except Exception as e:
                            ce_ltp = "Err"
                            st.session_state['debug_token'] = ce_token
                            st.session_state['debug_seg'] = ce_seg if ce_seg else "nse_fo"
                            if st.checkbox("Show CE Error", key="ce_err"):
                                st.write(f"Token: {ce_token}, Seg: {ce_seg}")
                                st.write(e)
                    st.metric("CE LTP", ce_ltp)

                with col4:
                    pe_def_idx = st.session_state.get('pe_idx_val', len(strikes)//2 if strikes else 0)
                    if pe_def_idx >= len(strikes): pe_def_idx = 0

                    pe_strike = st.selectbox("PE Strike", strikes, index=pe_def_idx, format_func=lambda x: f"{float(x):g}", key="pe_strike_box")
                    # Get LTP for PE
                    pe_token, pe_seg = get_token_and_segment(df_indices, symbol, expiry, pe_strike, "PE")
                    pe_ltp = "Loading..."

                    if pe_token:
                        try:
                            seg = pe_seg if pe_seg else "nse_fo"
                            q = client.quotes(instrument_tokens=[{"instrument_token": pe_token, "exchange_segment": seg}], quote_type="ltp")
                            pe_ltp = extract_ltp(q)

                            # Always allow viewing raw response
                            with st.expander("Raw PE Response"):
                                st.write(q)

                        except Exception as e:
                            pe_ltp = "Err"
                            if st.checkbox("Show PE Error", key="pe_err"):
                                st.write(f"Token: {pe_token}, Seg: {pe_seg}")
                                st.write(e)
                    st.metric("PE LTP", pe_ltp)

                with col5:
                    stop_loss = st.number_input("Stop Loss Points", min_value=0.0, step=0.5, value=10.0)
                    quantity = st.number_input("Quantity", min_value=1, step=1, value=50) # Default lot size?

                # Buttons Row
                st.markdown("---")
                b_col1, b_col2, b_col3, b_col4 = st.columns(4)

                def place_dashboard_order(transaction_type, option_type, strike, token, quantity, stop_loss, ltp_ref):
                    if not token:
                        st.error("Invalid Instrument Token")
                        return

                    st.toast(f"Placing {transaction_type} order for {symbol} {expiry} {strike} {option_type}...")

                    product_type = "MIS"

                    try:
                        # Get Trading Symbol
                        subset = df_indices[
                            (df_indices['symbol'] == symbol) &
                            (df_indices['expiry'] == expiry) &
                            (df_indices['strike'] == strike) &
                            (df_indices['option_type'] == option_type)
                        ]
                        if subset.empty:
                            st.error("Trading Symbol not found")
                            return

                        trading_sym = subset['trading_symbol'].values[0]

                        # 1. Place Entry Order (Market)
                        order_args = {
                            "exchange_segment": "nse_fo",
                            "product": product_type,
                            "price": "0", # Market order
                            "order_type": "MKT",
                            "quantity": str(quantity),
                            "validity": "DAY",
                            "trading_symbol": trading_sym,
                            "transaction_type": "B" if transaction_type == "BUY" else "S",
                            "amo": "NO"
                        }

                        # Call API for Entry
                        resp = client.place_order(**order_args)

                        if resp and 'nOrdNo' in resp: # Check for success key
                             st.success(f"Entry Order Placed! ID: {resp['nOrdNo']}")

                             # 2. Place Stop Loss Order (SL-M) if needed
                             if stop_loss > 0:
                                 try:
                                     # Convert LTP to float
                                     current_price = float(ltp_ref)

                                     # Calculate Trigger Price
                                     if transaction_type == "BUY":
                                         # Long Entry -> SL is Sell below Entry
                                         trigger_price = current_price - stop_loss
                                         sl_transaction_type = "S"
                                     else:
                                         # Short Entry -> SL is Buy above Entry
                                         trigger_price = current_price + stop_loss
                                         sl_transaction_type = "B"

                                     # Round trigger price to valid tick size (usually 0.05)
                                     trigger_price = round(trigger_price * 20) / 20

                                     if trigger_price <= 0:
                                         st.warning(f"Calculated SL Trigger Price ({trigger_price}) is invalid. SL Order skipped.")
                                     else:
                                         # Calculate Limit Price for SL order (SL-L)
                                         # For Sell SL: Limit should be <= Trigger (to ensure fill)
                                         # For Buy SL: Limit should be >= Trigger
                                         buffer_points = 2.0 # Fixed buffer to ensure execution

                                         if sl_transaction_type == "S":
                                             sl_limit_price = trigger_price - buffer_points
                                         else:
                                             sl_limit_price = trigger_price + buffer_points

                                         # Round to tick size
                                         sl_limit_price = round(sl_limit_price * 20) / 20

                                         # Ensure price is valid
                                         if sl_limit_price <= 0: sl_limit_price = 0.05

                                         sl_args = {
                                            "exchange_segment": "nse_fo",
                                            "product": product_type,
                                            "price": str(sl_limit_price), # SL-L requires a limit price
                                            "order_type": "SL", # Changed from SL-M to SL (Stop Limit)
                                            "quantity": str(quantity),
                                            "validity": "DAY",
                                            "trading_symbol": trading_sym,
                                            "transaction_type": sl_transaction_type,
                                            "trigger_price": str(trigger_price),
                                            "amo": "NO"
                                         }

                                         sl_resp = client.place_order(**sl_args)
                                         if sl_resp and 'nOrdNo' in sl_resp:
                                             st.info(f"Stop Loss Limit Order Placed! ID: {sl_resp['nOrdNo']} Trigger: {trigger_price}, Limit: {sl_limit_price}")
                                         else:
                                             st.warning(f"Stop Loss Order Failed: {sl_resp.get('Error', sl_resp)}")

                                 except ValueError:
                                     st.warning("Invalid LTP for Stop Loss calculation. SL Order skipped.")
                                 except Exception as sl_ex:
                                     st.warning(f"Exception placing SL Order: {sl_ex}")

                        elif resp and 'Error' in resp:
                             st.error(f"Order Failed: {resp['Error']}")
                        else:
                             st.info(f"Order Response: {resp}")

                    except Exception as ex:
                        st.error(f"Exception placing order: {ex}")

                with b_col1:
                    if st.button("BUY CALL", use_container_width=True, type="primary"):
                        place_dashboard_order("BUY", "CE", ce_strike, ce_token, quantity, stop_loss, ce_ltp)
                with b_col2:
                    if st.button("SELL CALL", use_container_width=True):
                        place_dashboard_order("SELL", "CE", ce_strike, ce_token, quantity, stop_loss, ce_ltp)
                with b_col3:
                    if st.button("BUY PUT", use_container_width=True, type="primary"):
                        place_dashboard_order("BUY", "PE", pe_strike, pe_token, quantity, stop_loss, pe_ltp)
                with b_col4:
                    if st.button("SELL PUT", use_container_width=True):
                        place_dashboard_order("SELL", "PE", pe_strike, pe_token, quantity, stop_loss, pe_ltp)

        except Exception as e:
            st.error(f"Error processing data: {e}")
            if st.checkbox("Show Error Details"):
                 st.write(e)

else:
    st.info("Please login from the sidebar to continue.")
