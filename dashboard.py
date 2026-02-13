import streamlit as st
import pandas as pd
from neo_api_client import NeoAPI
from logic import load_scrip_master, filter_data_for_indices, get_expiry_list, get_strikes, get_token

# Page configuration
st.set_page_config(page_title="Kotak Neo Quick Dashboard", layout="wide")

# Sidebar for Authentication
with st.sidebar:
    st.header("Authentication")
    consumer_key = st.text_input("Consumer Key", type="password", help="From Kotak Neo Trade API settings")
    mobile_number = st.text_input("Mobile Number", help="Registered Mobile Number with Country Code (e.g., +91...)")
    password = st.text_input("Password", type="password", help="Your account password")
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
                client.totp_login(mobile_number=mobile_number, ucc=ucc, totp=totp)
                client.totp_validate(mpin=mpin)
                st.session_state['client'] = client
                st.session_state['ucc'] = ucc
                st.success("Logged in successfully!")
                st.rerun()
            except Exception as e:
                st.error(f"Login failed: {e}")

# Main Content
st.title("Kotak Neo Quick Options Dashboard")

if 'client' in st.session_state:
    client = st.session_state['client']
    st.success(f"Connected as {st.session_state.get('ucc', 'Unknown')}")

    if st.button("Logout"):
        del st.session_state['client']
        st.rerun()

    # Fetch Scrip Master
    df_master = None
    try:
        with st.spinner("Loading Scrip Master..."):
            df_master = load_scrip_master(client, "nse_fo")
    except Exception as e:
        st.error(f"Error loading Scrip Master: {e}")

    if df_master is not None:
        # Debug Mode Toggle
        if st.sidebar.checkbox("Show Raw Scrip Master Columns"):
            st.write("Raw Columns:", df_master.columns.tolist())
            st.dataframe(df_master.head())

        # Process Data using logic.py
        try:
            df_indices = filter_data_for_indices(df_master)

            if df_indices.empty and not df_master.empty:
                 st.warning("No indices found after filtering. Check if Scrip Master format has changed.")

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

                col3, col4, col5 = st.columns(3)

                with col3:
                    ce_strike = st.selectbox("CE Strike", strikes, index=len(strikes)//2 if strikes else 0)
                    # Get LTP for CE
                    ce_token = get_token(df_indices, symbol, expiry, ce_strike, "CE")
                    ce_ltp = "Loading..."

                    if ce_token:
                        try:
                            q = client.quotes(instrument_tokens=[{"instrument_token": ce_token, "exchange_segment": "nse_fo"}], quote_type="ltp")
                            if q and 'data' in q and len(q['data']) > 0:
                                ce_ltp = q['data'][0].get('ltp', 'N/A')
                            else:
                                ce_ltp = "N/A"
                        except Exception:
                            ce_ltp = "Err"
                    st.metric("CE LTP", ce_ltp)

                with col4:
                    pe_strike = st.selectbox("PE Strike", strikes, index=len(strikes)//2 if strikes else 0)
                    # Get LTP for PE
                    pe_token = get_token(df_indices, symbol, expiry, pe_strike, "PE")
                    pe_ltp = "Loading..."

                    if pe_token:
                        try:
                            q = client.quotes(instrument_tokens=[{"instrument_token": pe_token, "exchange_segment": "nse_fo"}], quote_type="ltp")
                            if q and 'data' in q and len(q['data']) > 0:
                                pe_ltp = q['data'][0].get('ltp', 'N/A')
                            else:
                                pe_ltp = "N/A"
                        except:
                            pe_ltp = "Err"
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
                                         sl_args = {
                                            "exchange_segment": "nse_fo",
                                            "product": product_type,
                                            "price": "0", # SL-M means Market after Trigger
                                            "order_type": "SL-M",
                                            "quantity": str(quantity),
                                            "validity": "DAY",
                                            "trading_symbol": trading_sym,
                                            "transaction_type": sl_transaction_type,
                                            "trigger_price": str(trigger_price),
                                            "amo": "NO"
                                         }

                                         sl_resp = client.place_order(**sl_args)
                                         if sl_resp and 'nOrdNo' in sl_resp:
                                             st.info(f"Stop Loss Order Placed! ID: {sl_resp['nOrdNo']} at Trigger: {trigger_price}")
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
