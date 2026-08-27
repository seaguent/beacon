import json
import random
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FlakyHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)

        roll = random.random()
        if roll < 0.80:
            self._respond(200)
        elif roll < 0.90:
            self._respond(500)
        elif roll < 0.95:
            time.sleep(12)  # exceeds the worker's 10s request timeout
            self._respond(200)
        else:
            self.close_connection = True  # abrupt disconnect, no response sent

    def _respond(self, status: int) -> None:
        body = json.dumps({"ok": status == 200}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # suppress default per-request console logging


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", 9000), FlakyHandler)
    print("Flaky receiver listening on :9000 (80% ok / 10% 500 / 5% slow / 5% disconnect)")
    server.serve_forever()
