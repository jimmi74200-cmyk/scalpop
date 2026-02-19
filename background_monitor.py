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

        self.targets = {} # {order_id: {target_price, token, transaction_type, symbol, quantity, segment, status, last_ltp, error, last_poll_time}}
        self.pending_entries = {} # {entry_id: {sl_params: dict, status: str, check_count: int}}
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

    def add_pending_entry(self, order_id, sl_params):
        """
        Monitors an entry order. When it fills (TRADED), places the SL order defined in sl_params.
        """
        with self.lock:
            self.pending_entries[str(order_id)] = {
                "sl_params": sl_params,
                "status": "Pending Fill",
                "check_count": 0
            }
        print(f"Pending Entry added for monitoring: {order_id}")

    def _monitor_loop(self):
        while self.is_running:
            if not self.client:
                time.sleep(1)
                continue

            # 1. Check Pending Entries (for Limit Orders)
            self._check_pending_entries()

            # 2. Check Targets (for Exit)
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
                                    # print(f"Polled LTP for {data['symbol']}: {ltp}")

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
                                 pass
                            else:
                                self.targets[oid]["status"] = "Active"

                    if ltp is not None and ltp > 0:
                        hit = False
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

    def _check_pending_entries(self):
        try:
            with self.lock:
                entry_ids = list(self.pending_entries.keys())

            for eid in entry_ids:
                try:
                    # Poll status
                    resp = self.client.order_history(order_id=eid)
                    # Response is usually: {'data': [{'order_id': ..., 'stat': 'TRADED', ...}, ...]}
                    # Order history list is sorted? Usually last item is latest.

                    status = None
                    if resp and 'data' in resp and resp['data']:
                        # Get latest status (assuming list or single dict)
                        orders = resp['data']
                        if isinstance(orders, list):
                            # Usually 0 is latest or last is latest?
                            # Kotak API usually returns list of state changes.
                            # We look for ANY 'TRADED' status in the history logic?
                            # Or just the status of the order.
                            # Let's check the 'stat' or 'ordSt' of the first item (often current state).
                            # Actually, order_report is status snapshot, order_history is history.
                            # Let's assume order_history returns list. We check if any item is TRADED.
                            for o in orders:
                                st_code = str(o.get('stat', o.get('ordSt', ''))).upper()
                                if st_code == 'TRADED' or st_code == 'COMPLETE':
                                    status = 'TRADED'
                                    break
                                elif st_code in ['CANCELLED', 'REJECTED', 'ABORTED']:
                                    status = 'FAILED'
                        elif isinstance(orders, dict):
                             st_code = str(orders.get('stat', orders.get('ordSt', ''))).upper()
                             if st_code == 'TRADED' or st_code == 'COMPLETE': status = 'TRADED'
                             elif st_code in ['CANCELLED', 'REJECTED', 'ABORTED']: status = 'FAILED'

                    if status == 'TRADED':
                        print(f"Pending Entry {eid} FILLED. Placing SL...")
                        with self.lock:
                            sl_params = self.pending_entries[eid]['sl_params']

                        # Place SL Order
                        sl_resp = self.client.place_order(**sl_params)
                        if sl_resp and 'nOrdNo' in sl_resp:
                            print(f"Auto SL Order Placed: {sl_resp['nOrdNo']}")
                            with self.lock:
                                del self.pending_entries[eid]
                        else:
                            print(f"Auto SL Order FAILED: {sl_resp}")
                            # Should we retry? For now, leave it in pending but maybe flag error?
                            # If we leave it, it will try again next loop (which is good for transient errors)
                            # But if persistent error, we might spam.
                            # Let's retry max 3 times then drop?
                            pass

                    elif status == 'FAILED':
                        print(f"Pending Entry {eid} FAILED/CANCELLED. Removing monitor.")
                        with self.lock:
                            del self.pending_entries[eid]

                except Exception as e:
                    print(f"Error checking entry {eid}: {e}")

        except Exception as ex:
            print(f"Check Pending Entries Error: {ex}")

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
