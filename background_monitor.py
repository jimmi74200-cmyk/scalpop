import threading
import time
from socket_manager import ws_manager

class BackgroundMonitor:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(BackgroundMonitor, cls).__new__(cls)
                    cls._instance.initialized = False
        return cls._instance

    def __init__(self):
        if self.initialized:
            return

        self.targets = {} # {order_id: {target_price, token, transaction_type, symbol, quantity, segment, status, last_ltp, error}}
        self.client = None
        self.is_running = False
        self.thread = None
        self.lock = threading.Lock()
        self.initialized = True

    def set_client(self, client):
        self.client = client

    def start(self):
        if self.is_running:
            return

        self.is_running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.thread.start()
        print("Background Monitor Started")

    def stop(self):
        self.is_running = False
        if self.thread:
            self.thread.join(timeout=2)
        print("Background Monitor Stopped")

    def add_target(self, order_id, target_price, token, transaction_type, symbol, quantity, segment="nse_fo"):
        with self.lock:
            self.targets[str(order_id)] = {
                "target": float(target_price),
                "token": str(token),
                "type": transaction_type,
                "symbol": symbol,
                "qty": quantity,
                "segment": segment,
                "status": "Monitoring",
                "last_ltp": None,
                "error": None,
                "last_poll_time": 0
            }
        # Ensure subscription in WS manager
        if self.client:
            inst = [{"instrument_token": str(token), "exchange_segment": segment}]
            ws_manager.subscribe(self.client, inst)
        print(f"Target added for {symbol} ({order_id}): {target_price}")

    def remove_target(self, order_id):
        with self.lock:
            if str(order_id) in self.targets:
                del self.targets[str(order_id)]
                print(f"Target removed for {order_id}")

    def _monitor_loop(self):
        while self.is_running:
            if not self.client:
                time.sleep(1)
                continue

            try:
                # Iterate over a copy of items to avoid modification issues
                with self.lock:
                    items = list(self.targets.items())

                for oid, data in items:
                    token = data["token"]
                    target_price = data["target"]
                    trans_type = data["type"]

                    # 1. Try WebSocket LTP (returns (ltp, timestamp))
                    ltp, ltp_time = ws_manager.get_ltp(token)

                    # 2. Fallback: Polling if LTP is stale/missing (every 5 seconds)
                    now = time.time()
                    should_poll = False

                    if ltp is None or ltp == 0:
                        should_poll = True
                    elif ltp_time and (now - ltp_time > 5):
                        should_poll = True

                    if should_poll:
                        last_poll = data.get("last_poll_time", 0)
                        if now - last_poll > 5:
                            # Update status to indicate polling
                            with self.lock:
                                if oid in self.targets:
                                     self.targets[oid]["status"] = "Polling (Stale/Missing WS Data)..."

                            try:
                                # Fetch Quote via HTTP REST API
                                q_resp = self.client.quotes(instrument_tokens=[{"instrument_token": token, "exchange_segment": data["segment"]}], quote_type="ltp")
                                # Extract LTP from response (reuse logic similar to dashboard but simplified)
                                # The response structure is tricky, let's look for 'ltp' or 'last_price'
                                polled_ltp = None

                                # Recursive search for LTP
                                def find_ltp_recursive(obj):
                                    if isinstance(obj, dict):
                                        for k, v in obj.items():
                                            if str(k).lower() in ['ltp', 'last_price', 'lp', 'close']: return v
                                            res = find_ltp_recursive(v)
                                            if res: return res
                                    elif isinstance(obj, list):
                                        for item in obj:
                                            res = find_ltp_recursive(item)
                                            if res: return res
                                    return None

                                polled_ltp = find_ltp_recursive(q_resp)

                                if polled_ltp:
                                    ltp = float(polled_ltp)
                                    # Update WS manager too so it persists
                                    ws_manager.update_ltp(token, ltp)
                                    print(f"Polled LTP for {data['symbol']}: {ltp}")

                                # Update poll time
                                with self.lock:
                                    if oid in self.targets:
                                        self.targets[oid]["last_poll_time"] = now
                            except Exception as poll_e:
                                print(f"Polling Error for {oid}: {poll_e}")

                    # Update LTP in target dict for UI
                    with self.lock:
                        if oid in self.targets:
                            self.targets[oid]["last_ltp"] = ltp
                            if ltp is None:
                                self.targets[oid]["status"] = "Waiting for Data..."
                            elif should_poll and (time.time() - data.get("last_poll_time", 0) < 2):
                                 # Keep "Polling..." status briefly visible
                                 pass
                            else:
                                self.targets[oid]["status"] = "Active"

                    if ltp is not None and ltp > 0:
                        hit = False
                        # Logic:
                        # SL-Sell (Long): Exit if LTP >= Target
                        # SL-Buy (Short): Exit if LTP <= Target
                        # Note: 'trans_type' here is the SL order type.
                        # If I have a Long Position, I place a SELL SL.
                        # So if SL is "SELL", I am exiting a Long. I want to exit if Price goes UP to Target.

                        if trans_type in ["S", "SELL"]:
                            if ltp >= target_price:
                                hit = True
                        elif trans_type in ["B", "BUY"]:
                            if ltp <= target_price:
                                hit = True

                        if hit:
                            print(f"Target HIT for {data['symbol']} (Order {oid}). LTP: {ltp}, Target: {target_price}")
                            with self.lock:
                                if oid in self.targets:
                                     self.targets[oid]["status"] = "Target HIT! Modifying..."

                            success = self._modify_to_market(oid, data)
                            if success:
                                self.remove_target(oid)
                            else:
                                with self.lock:
                                    if oid in self.targets:
                                        self.targets[oid]["status"] = "Modification Failed (Retrying...)"

            except Exception as e:
                print(f"Monitor Loop Error: {e}")

            time.sleep(1) # Check every 1 second

    def _modify_to_market(self, order_id, data):
        try:
            mod_args = {
                "order_id": str(order_id),
                "instrument_token": str(data["token"]),
                "order_type": "MKT",
                "quantity": str(data["qty"]),
                "price": "0",
                "trigger_price": "0",
                "validity": "DAY",
                "exchange_segment": data["segment"],
                "product": "MIS",
                "trading_symbol": data["symbol"],
                "transaction_type": data["type"]
            }

            print(f"Modifying Order {order_id} to Market...")
            resp = self.client.modify_order(**mod_args)

            # Check for success (usually nOrdNo is returned)
            if resp and ('nOrdNo' in resp or 'result' in resp):
                # API v2 often returns nOrdNo on success, or maybe 'result': 'ok'
                # Check for error keys explicitly
                if 'Error' in resp or 'error' in resp:
                     err_msg = resp.get('Error', resp.get('error'))
                     with self.lock:
                        if str(order_id) in self.targets:
                            self.targets[str(order_id)]["error"] = str(err_msg)
                     print(f"Order {order_id} Modification FAILED: {err_msg}")
                     return False

                print(f"Order {order_id} Modification SUCCESS")
                return True
            else:
                # Handle unknown response format
                msg = f"Unknown Resp: {resp}"
                with self.lock:
                    if str(order_id) in self.targets:
                        self.targets[str(order_id)]["error"] = msg
                print(f"Order {order_id} Modification FAILED: {msg}")
                return False

        except Exception as e:
            print(f"Exception modifying order {order_id}: {e}")
            with self.lock:
                if str(order_id) in self.targets:
                    self.targets[str(order_id)]["error"] = str(e)
            return False

# Singleton
bg_monitor = BackgroundMonitor()
