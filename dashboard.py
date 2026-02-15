import streamlit as st
import pandas as pd
import sys
import time
import math
from socket_manager import ws_manager # Import singleton
from background_monitor import bg_monitor # Import singleton

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

    # Scrip Master Debug Tool
    if st.checkbox("Show Scrip Master Info"):
        st.subheader("Scrip Master Files")
        if st.button("Get File URLs"):
            if 'client' in st.session_state:
                try:
                    url_fo = st.session_state['client'].scrip_master(exchange_segment="nse_fo")
                    st.write("NSE FO URL:", url_fo)
                    url_cm = st.session_state['client'].scrip_master(exchange_segment="nse_cm")
                    st.write("NSE CM URL:", url_cm)
                except Exception as e:
                    st.error(f"Error fetching URLs: {e}")
            else:
                st.error("Please login first")

        if 'df_master' in locals() and df_master is not None:
            st.download_button(
                label="Download Processed Data (First 1000 rows)",
                data=df_master.head(1000).to_csv(index=False).encode('utf-8'),
                file_name='scrip_master_sample.csv',
                mime='text/csv',
            )
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

                        # Initialize Background Monitor
                        bg_monitor.set_client(client)
                        bg_monitor.start()

                        st.success("Logged in successfully!")
                        st.rerun()
            except Exception as e:
                st.error(f"Login process failed: {e}")

# Helper: Modify SL Order to Market
def modify_to_market(order_id, symbol, qty, trans_type, segment="nse_fo"):
    try:
        # According to API v2 docs (inferred), modify usually takes params
        # To exit at market, we change order type to MKT and price/trigger to 0
        mod_args = {
            "order_id": str(order_id),
            "order_type": "MKT",
            "quantity": str(qty),
            "price": "0",
            "trigger_price": "0",
            "validity": "DAY",
            "exchange_segment": segment,
            "product": "MIS", # Assuming MIS for intraday
            "trading_symbol": symbol,
            "transaction_type": trans_type
        }

        # Call modify
        resp = st.session_state['client'].modify_order(**mod_args)
        if resp and 'nOrdNo' in resp:
            st.toast(f"Order {order_id} modified to Market Exit!")
            return True
        else:
            st.error(f"Modify Failed: {resp}")
            return False
    except Exception as e:
        st.error(f"Exception modifying order: {e}")
        return False

# Main Content
st.title("Kotak Neo Quick Options Dashboard")

if 'client' in st.session_state:
    client = st.session_state['client']

    # Ensure BG Monitor is connected and running (handles page refresh)
    bg_monitor.set_client(client)
    bg_monitor.start()

    st.success(f"Connected as {st.session_state.get('ucc', 'Unknown')}")

    if st.button("Logout"):
        del st.session_state['client']
        st.rerun()

    # --- Pending Orders Section ---
    with st.expander("Pending Orders & Targets", expanded=True):
        col_ref = st.columns([1])[0]
        with col_ref:
            if st.button("Refresh Orders"):
                st.rerun()

        # Define core rendering logic (UI only)
        def render_pending_orders_ui():
            # Fetch Orders
            try:
                orders_resp = client.order_report()
                if orders_resp and 'data' in orders_resp:
                    all_orders = orders_resp['data']
                    pending_orders = [
                        o for o in all_orders
                        if str(o.get('ordSt', o.get('order_status', ''))).lower() in ['trigger_pending', 'trig_pending', 'pending', 'open']
                    ]

                    if pending_orders:
                        # Create UI for each order
                        for order in pending_orders:
                            oid = str(order.get('nOrdNo', order.get('order_id', 'Unknown')))
                            sym = order.get('trdSym', order.get('trading_symbol', 'Unknown'))
                            typ = order.get('trns', order.get('transaction_type', '')) # B/S
                            qty = order.get('qty', order.get('quantity', 0))
                            prc = order.get('trigPrc', order.get('trigger_price', 0))
                            token = order.get('tok')

                            # Row
                            c1, c2, c3, c4, c5 = st.columns([2, 1, 1, 2, 2])
                            with c1:
                                st.write(f"**{sym}** ({typ})")
                                st.caption(f"ID: {oid} | Qty: {qty}")
                            with c2:
                                st.write(f"Trig: {prc}")
                            with c3:
                                if st.button("Exit MKT", key=f"exit_{oid}"):
                                    modify_to_market(oid, sym, qty, typ)
                                    bg_monitor.remove_target(oid)
                                    st.rerun()

                            # Check if monitored
                            is_monitored = oid in bg_monitor.targets

                            with c4:
                                if is_monitored:
                                    tgt = bg_monitor.targets[oid]['target']
                                    st.info(f"Active Target: {tgt}")
                                else:
                                    # Target Input
                                    st.number_input("Target", value=0.0, key=f"tgt_in_{oid}", step=0.5)

                            with c5:
                                if is_monitored:
                                    if st.button("Cancel Monitor", key=f"cancel_{oid}"):
                                        bg_monitor.remove_target(oid)
                                        st.rerun()
                                else:
                                    if st.button("Set Target", key=f"set_{oid}"):
                                        target_val = st.session_state.get(f"tgt_in_{oid}", 0.0)
                                        if token and target_val > 0:
                                            bg_monitor.add_target(oid, target_val, token, typ, sym, qty)
                                            st.success(f"Monitoring {sym} @ {target_val}")
                                            st.rerun()
                                        elif target_val <= 0:
                                            st.warning("Please set a valid target > 0")
                                        else:
                                            st.error("Token missing for order")
                            st.divider()
                    else:
                        st.info("No pending orders found.")
                else:
                    st.info("No orders found or error fetching.")
                    if st.checkbox("Show Raw Order Response"):
                        st.write(orders_resp)
            except Exception as e:
                st.error(f"Error fetching orders: {e}")

        render_pending_orders_ui()

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

                            # PRIORITIZE EXACT MATCH on 'symbol' first
                            # This prevents "NIFTY" from matching "MIDCPNIFTY" or "NIFTYNXT50"

                            fut_subset = pd.DataFrame()

                            # 1. Try Exact Symbol Match + FUT
                            exact_symbol_futs = df_indices[
                                (df_indices['symbol'] == symbol) &
                                (df_indices['instrument_type'].astype(str).str.contains("FUT", case=False, na=False))
                            ].copy()

                            # 2. If no exact match, try Root Symbol Match + FUT (be stricter)
                            if exact_symbol_futs.empty:
                                exact_symbol_futs = df_indices[
                                    (df_indices['symbol'].str.startswith(root_symbol)) &
                                    (df_indices['instrument_type'].astype(str).str.contains("FUT", case=False, na=False))
                                ].copy()

                            if not exact_symbol_futs.empty:
                                # Try exact expiry first
                                exact_futs = exact_symbol_futs[exact_symbol_futs['expiry'] == expiry]
                                if not exact_futs.empty:
                                    fut_subset = exact_futs.head(1)
                                else:
                                    # Find nearest future expiring >= option expiry
                                    exact_symbol_futs['expiry_dt'] = pd.to_datetime(exact_symbol_futs['expiry'], format='%d%b%Y', errors='coerce')
                                    curr_opt_expiry = pd.to_datetime(expiry, format='%d%b%Y', errors='coerce')

                                    if pd.notna(curr_opt_expiry):
                                        future_futs = exact_symbol_futs[exact_symbol_futs['expiry_dt'] >= curr_opt_expiry].sort_values('expiry_dt')
                                        if not future_futs.empty:
                                            fut_subset = future_futs.head(1)

                            # Fallback: Try finding Spot Index if Future not found
                            if fut_subset.empty:
                                # Strategy: Look for anything in NSE_CM that matches the symbol EXACTLY first
                                # We trust segment 'nse_cm' more than 'instrument_type'

                                # 1. Filter by segment 'cm'
                                cm_subset = df_indices[
                                    df_indices['exchange_segment'].astype(str).str.contains('cm', case=False, na=False)
                                ]

                                if not cm_subset.empty:
                                    # 2. Filter by Exact Symbol first
                                    idx_subset = cm_subset[cm_subset['symbol'] == symbol].copy()

                                    if idx_subset.empty:
                                         # Fallback to contains
                                         idx_subset = cm_subset[
                                            cm_subset['symbol'].str.contains(root_symbol, case=False, na=False)
                                         ].copy()

                                    if not idx_subset.empty:
                                        # Prefer short names (e.g. "NIFTY 50" over "NIFTY 50 ...")
                                        # Or just take the first one
                                        fut_subset = idx_subset.head(1)
                                        st.info(f"Using Spot Index ({fut_subset.iloc[0]['symbol']}) as reference.")
                                    else:
                                        # If root symbol match failed, maybe try fuzzy?
                                        pass

                            # Debug info if still failed
                            if fut_subset.empty:
                                st.warning(f"No Future or Index found for {symbol} (Root: {root_symbol}).")
                                # Extended Debugging
                                st.write("Root Symbol:", root_symbol)
                                if 'instrument_type' in df_indices.columns:
                                    st.write("Available Types:", df_indices['instrument_type'].unique())
                                if 'exchange_segment' in df_indices.columns:
                                    st.write("Available Segments:", df_indices['exchange_segment'].unique())

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
                            fut_token_debug = ""
                            fut_seg_debug = ""
                            q_debug = {}

                            if not fut_subset.empty:
                                fut_token = str(fut_subset.iloc[0]['instrument_token']).strip()
                                # Fix: Ensure exchange_segment is a simple string, handling Series or duplicate columns
                                fut_seg_raw = fut_subset.iloc[0]['exchange_segment']
                                if isinstance(fut_seg_raw, pd.Series):
                                     fut_seg_raw = fut_seg_raw.iloc[0]
                                fut_seg = str(fut_seg_raw).strip().lower()

                                if not fut_seg or fut_seg == 'nan': fut_seg = "nse_fo"
                                # If it's an index, seg might be nse_cm. Ensure we use the row's segment.

                                fut_token_debug = fut_token
                                fut_seg_debug = fut_seg

                                # Fetch LTP
                                q = client.quotes(instrument_tokens=[{"instrument_token": fut_token, "exchange_segment": fut_seg}], quote_type="ltp")
                                q_debug = q

                                # Mini-extractor
                                def quick_extract(resp):
                                    if isinstance(resp, dict):
                                        for k,v in resp.items():
                                            if str(k).strip().lower() in ['ltp', 'last_price', 'last_traded_price', 'close', 'lp']: return v
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

                                # Store selected strike values instead of indices
                                st.session_state['ce_selected_strike'] = strikes[ce_idx]
                                st.session_state['pe_selected_strike'] = strikes[pe_idx]
                                st.rerun()
                            else:
                                st.warning("Could not fetch Reference Price (Future) to auto-select.")
                                with st.expander("Show Debug Details"):
                                    st.write(f"Token: {fut_token_debug}")
                                    st.write(f"Segment: {fut_seg_debug}")
                                    st.write("Full Response:", q_debug)
                                    if not fut_subset.empty:
                                        st.write("Selected Row:", fut_subset.iloc[0].to_dict())

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

                # Helper for robust check
                def find_best_match(target, candidates):
                    if target is None: return None
                    if not candidates: return None

                    # 1. Exact match
                    if target in candidates:
                        return target

                    # 2. Close match (for floats)
                    # candidates are likely sorted floats
                    for c in candidates:
                        if math.isclose(c, target, abs_tol=0.01):
                            return c

                    return None

                # Helper to filter strikes around a target
                def get_filtered_strikes(all_strikes, target_strike, window=30):
                    if not all_strikes: return [], 0

                    # Ensure all_strikes is sorted, though get_strikes does it, just in case
                    all_strikes = sorted(all_strikes)

                    if target_strike not in all_strikes:
                        # Find closest if target not in list (handling float/int mismatches)
                        try:
                            closest = min(all_strikes, key=lambda x: abs(x - target_strike))
                            target_index = all_strikes.index(closest)
                            # Update target_strike to the found closest match for consistent subset logic
                            target_strike = closest
                        except ValueError:
                             target_index = len(all_strikes) // 2
                    else:
                        target_index = all_strikes.index(target_strike)

                    start_idx = max(0, target_index - window)
                    end_idx = min(len(all_strikes), target_index + window + 1)

                    subset = all_strikes[start_idx:end_idx]

                    # New index of target in subset
                    try:
                        new_index = subset.index(target_strike)
                    except ValueError:
                         new_index = len(subset) // 2

                    return subset, new_index

                # Helper to update strike via button callback
                def shift_strike(key, strikes_list, direction):
                    # Get current value from session state
                    current_val = st.session_state.get(key)

                    # Find current index
                    try:
                        if current_val in strikes_list:
                            curr_idx = strikes_list.index(current_val)
                        else:
                            # If somehow value mismatch, find closest
                            closest = min(strikes_list, key=lambda x: abs(x - float(current_val or 0)))
                            curr_idx = strikes_list.index(closest)

                        # Calculate new index
                        new_idx = max(0, min(len(strikes_list)-1, curr_idx + direction))
                        st.session_state[key] = strikes_list[new_idx]

                        # Also update shadow selection if we use it
                        if key == 'ce_strike_box':
                            st.session_state['ce_selected_strike'] = strikes_list[new_idx]
                        elif key == 'pe_strike_box':
                            st.session_state['pe_selected_strike'] = strikes_list[new_idx]

                    except Exception as ex:
                        pass # Ignore if something goes wrong during callback

                with col3:
                    # Determine current CE target
                    # 1. Check if user manually selected one (in session state via key)
                    raw_ce_target = st.session_state.get('ce_strike_box')
                    matched_ce_target = find_best_match(raw_ce_target, strikes)

                    # 2. If valid user selection found, use it and SYNC session state
                    if matched_ce_target is not None:
                        current_ce_target = matched_ce_target
                        # Force session state to match the exact object in the new list
                        if st.session_state.get('ce_strike_box') != matched_ce_target:
                             st.session_state['ce_strike_box'] = matched_ce_target
                    else:
                        # 3. Fallback to auto-select
                        raw_auto_ce = st.session_state.get('ce_selected_strike')
                        matched_auto_ce = find_best_match(raw_auto_ce, strikes)

                        if matched_auto_ce is not None:
                             current_ce_target = matched_auto_ce
                        else:
                             current_ce_target = strikes[len(strikes)//2] if strikes else 0

                    # 4. Filter list centered on target
                    ce_strikes_subset, ce_subset_idx = get_filtered_strikes(strikes, current_ce_target, window=30)

                    # 5. Render Buttons and Dropdown
                    cc1, cc2, cc3 = st.columns([1, 4, 1])
                    with cc1:
                        st.button("➖", key="ce_minus", help="Shift Strike Down", on_click=shift_strike, args=('ce_strike_box', strikes, -1))
                    with cc2:
                        ce_strike = st.selectbox("CE Strike", ce_strikes_subset, index=ce_subset_idx, format_func=lambda x: f"{float(x):g}", key="ce_strike_box")
                    with cc3:
                        st.button("➕", key="ce_plus", help="Shift Strike Up", on_click=shift_strike, args=('ce_strike_box', strikes, 1))
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
                    # Determine current PE target
                    raw_pe_target = st.session_state.get('pe_strike_box')
                    matched_pe_target = find_best_match(raw_pe_target, strikes)

                    if matched_pe_target is not None:
                        current_pe_target = matched_pe_target
                        # Force session state to match
                        if st.session_state.get('pe_strike_box') != matched_pe_target:
                             st.session_state['pe_strike_box'] = matched_pe_target
                    else:
                        raw_auto_pe = st.session_state.get('pe_selected_strike')
                        matched_auto_pe = find_best_match(raw_auto_pe, strikes)

                        if matched_auto_pe is not None:
                             current_pe_target = matched_auto_pe
                        else:
                             current_pe_target = strikes[len(strikes)//2] if strikes else 0

                    # 4. Filter list centered on target
                    pe_strikes_subset, pe_subset_idx = get_filtered_strikes(strikes, current_pe_target, window=30)

                    # 5. Render Buttons and Dropdown
                    pc1, pc2, pc3 = st.columns([1, 4, 1])
                    with pc1:
                        st.button("➖", key="pe_minus", help="Shift Strike Down", on_click=shift_strike, args=('pe_strike_box', strikes, -1))
                    with pc2:
                        pe_strike = st.selectbox("PE Strike", pe_strikes_subset, index=pe_subset_idx, format_func=lambda x: f"{float(x):g}", key="pe_strike_box")
                    with pc3:
                        st.button("➕", key="pe_plus", help="Shift Strike Up", on_click=shift_strike, args=('pe_strike_box', strikes, 1))
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

                        # Debug Entry Order
                        with st.expander("Order Debug Details", expanded=True):
                            st.write("Entry Order Args:", order_args)

                        # Call API for Entry
                        resp = client.place_order(**order_args)

                        with st.expander("Order Debug Details", expanded=True):
                            st.write("Entry Order Response:", resp)

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

                                         with st.expander("Order Debug Details", expanded=True):
                                             st.write("SL Order Args:", sl_args)

                                         sl_resp = client.place_order(**sl_args)

                                         with st.expander("Order Debug Details", expanded=True):
                                             st.write("SL Order Response:", sl_resp)

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
