import json
import threading

class WebSocketManager:
    def __init__(self):
        self.latest_ltp = {}
        self.subscribed_tokens = set()
        self.is_running = False
        self.lock = threading.Lock()

    def on_message(self, message):
        """
        Callback for WebSocket messages.
        Parses the message and updates latest_ltp.
        """
        try:
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
            # print(f"WS Error parsing: {e}")
            pass

    def _process_feed_item(self, item):
        # Extract Token and LTP
        # Keys vary: 'tk', 'ltp' or 'instrument_token', 'last_traded_price'
        token = str(item.get('tk', item.get('instrument_token', '')))
        ltp = item.get('lp', item.get('ltp', item.get('last_traded_price', 0)))

        if token and ltp:
            with self.lock:
                self.latest_ltp[token] = float(ltp)

    def on_error(self, error):
        print(f"WS Error: {error}")

    def on_close(self, message):
        print(f"WS Closed: {message}")
        self.is_running = False

    def on_open(self, message):
        print(f"WS Opened: {message}")
        self.is_running = True

    def subscribe(self, client, tokens):
        """
        Subscribes to a list of tokens if not already subscribed.
        """
        if not tokens:
            return

        # Identify new tokens
        new_tokens = [str(t) for t in tokens if str(t) not in self.subscribed_tokens]

        if new_tokens:
            # Register callbacks if not already set or client changed
            # Note: We overwrite client callbacks. Ideally client is singleton per user.
            client.on_message = self.on_message
            client.on_error = self.on_error
            client.on_open = self.on_open
            client.on_close = self.on_close

            # Construct instrument_tokens list for API
            # Format: [{"instrument_token": "token", "exchange_segment": "nse_fo"}]
            # We assume nse_fo for simplicity or need to pass segment.
            # However, logic.py and dashboard.py usually know the segment.
            # For pure LTP monitoring of options, nse_fo is 99% likely.
            # If we need multi-segment, we need to change input to tuples.

            # The API requires a specific format.
            # client.subscribe(instrument_tokens=[...])
            # Let's assume standard format required by `subscribe` method.
            # Looking at neo_api.py: subscribe takes `instrument_tokens` list.
            # It passes it to NeoWebSocket.get_live_feed.

            sub_list = [{"instrument_token": t, "exchange_segment": "nse_fo"} for t in new_tokens]

            try:
                client.subscribe(instrument_tokens=sub_list)
                with self.lock:
                    self.subscribed_tokens.update(new_tokens)
            except Exception as e:
                print(f"Subscribe Error: {e}")

    def get_ltp(self, token):
        with self.lock:
            return self.latest_ltp.get(str(token))

# Singleton instance
ws_manager = WebSocketManager()
