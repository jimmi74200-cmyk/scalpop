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

        self.targets = {} # {order_id: {target_price, token, transaction_type, symbol, quantity, segment}}
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
                "segment": segment
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

                    # Get LTP
                    ltp = ws_manager.get_ltp(token)

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
                            success = self._modify_to_market(oid, data)
                            if success:
                                self.remove_target(oid)

            except Exception as e:
                print(f"Monitor Loop Error: {e}")

            time.sleep(1) # Check every 1 second

    def _modify_to_market(self, order_id, data):
        try:
            mod_args = {
                "order_id": str(order_id),
                "nOrdNo": str(order_id), # Explicitly add nOrdNo
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

            if resp and 'nOrdNo' in resp:
                print(f"Order {order_id} Modification SUCCESS")
                return True
            else:
                print(f"Order {order_id} Modification FAILED: {resp}")
                return False

        except Exception as e:
            print(f"Exception modifying order {order_id}: {e}")
            return False

# Singleton
bg_monitor = BackgroundMonitor()
