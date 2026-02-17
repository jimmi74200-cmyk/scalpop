import json
import threading
import time

class WebSocketManager:
    def __init__(self):
        self.latest_ltp = {}
        self.subscribed_tokens = set()
        self.is_running = False
        self.lock = threading.Lock()

        # Debug info
        self.last_message_raw = None
        self.last_error_message = None
        self.message_count = 0
        self.last_update_time = None

    def on_message(self, message):
        """
        Callback for WebSocket messages.
        Parses the message and updates latest_ltp.
        """
        try:
            with self.lock:
                self.message_count += 1
                self.last_update_time = time.time()
                # Store truncated raw message for debug (limit size)
                self.last_message_raw = str(message)[:500]

            # Message is usually a JSON string or list of dicts
            if isinstance(message, str):
                data = json.loads(message)
            else:
                data = message

            # Neo API feed format usually looks like list of dicts
            if isinstance(data, list):
                for item in data:
                    self._process_feed_item(item)
            elif isinstance(data, dict):
                self._process_feed_item(data)

        except Exception as e:
            with self.lock:
                self.last_error_message = f"Parse Error: {e}"

    def _process_feed_item(self, item):
        # Extract Token and LTP
        # Keys vary: 'tk', 'ltp' or 'instrument_token', 'last_traded_price'
        # Common keys in Neo API: 'tk', 'lp', 'ltp', 'ltt', 'pc', 'v'

        token = str(item.get('tk', item.get('instrument_token', '')))

        # LTP might be 'lp' or 'ltp' or 'last_traded_price'
        ltp = item.get('lp', item.get('ltp', item.get('last_traded_price')))

        if token and ltp is not None:
            try:
                val = float(ltp)
                with self.lock:
                    self.latest_ltp[token] = val
            except ValueError:
                pass

    def on_error(self, error):
        with self.lock:
            self.last_error_message = str(error)
        print(f"WS Error: {error}")

    def on_close(self, message):
        with self.lock:
            self.is_running = False
            self.last_error_message = f"Closed: {message}"
        print(f"WS Closed: {message}")

    def on_open(self, message):
        with self.lock:
            self.is_running = True
            self.last_error_message = None # Clear error on open
        print(f"WS Opened: {message}")

    def subscribe(self, client, instruments):
        """
        Subscribes to a list of instruments.
        instruments: list of dicts [{'instrument_token': '...', 'exchange_segment': '...'}]
        """
        if not instruments:
            return

        to_subscribe = []
        with self.lock:
            for inst in instruments:
                t = str(inst.get('instrument_token'))
                if t not in self.subscribed_tokens:
                     to_subscribe.append(inst)

        if to_subscribe:
            # Register callbacks if not already set or client changed
            client.on_message = self.on_message
            client.on_error = self.on_error
            client.on_open = self.on_open
            client.on_close = self.on_close

            try:
                # client.subscribe expects list of dicts
                client.subscribe(instrument_tokens=to_subscribe)
                with self.lock:
                    for inst in to_subscribe:
                        self.subscribed_tokens.add(str(inst.get('instrument_token')))
            except Exception as e:
                with self.lock:
                    self.last_error_message = f"Subscribe Exception: {e}"
                print(f"Subscribe Error: {e}")

    def get_ltp(self, token):
        with self.lock:
            return self.latest_ltp.get(str(token))

    def get_status(self):
        with self.lock:
            return {
                "connected": self.is_running,
                "msg_count": self.message_count,
                "last_update": self.last_update_time,
                "last_raw": self.last_message_raw,
                "last_err": self.last_error_message,
                "tokens": list(self.subscribed_tokens)
            }

# Singleton instance
ws_manager = WebSocketManager()
