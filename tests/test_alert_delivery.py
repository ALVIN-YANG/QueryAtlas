import base64
import hashlib
import hmac
import json
import tempfile
import threading
import unittest
from contextlib import closing
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from queryatlas import monitor


class AlertDelivery(unittest.TestCase):
    def test_dedup_ack_recovery_order_and_signed_retry(self):
        received = []

        class Sink(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(
                    json.loads(self.rfile.read(int(self.headers["content-length"])))
                )
                self.send_response(200)
                self.end_headers()
                self.wfile.write(
                    json.dumps({"code": 1 if len(received) == 1 else 0}).encode()
                )

            def log_message(self, *args):
                pass

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(monitor, "STATE", Path(tmp) / "state"),
            HTTPServer(("127.0.0.1", 0), Sink) as server,
        ):
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            self.addCleanup(server.shutdown)
            monitor.init_monitor()
            with closing(monitor.connect()) as c, c:
                for value in (True, True, False):
                    monitor.signal(c, "研发", "导入失败", "机密文档", value)
                self.assertEqual(
                    c.execute("SELECT count(*) FROM incidents").fetchone()[0], 1
                )
                self.assertEqual(
                    c.execute("SELECT count(*) FROM delivery").fetchone()[0], 2
                )
            now = int(monitor.time.time())
            url = f"http://127.0.0.1:{server.server_port}"
            for delta in (0, 1, 11, 12):
                monitor.deliver(url, "test-only", now + delta)
            self.assertEqual(len(received), 3)
            self.assertIn("触发", received[1]["content"]["text"])
            self.assertIn("恢复", received[2]["content"]["text"])
            self.assertNotIn("机密文档", str(received))
            key = f"{now + 11}\ntest-only".encode()
            self.assertEqual(
                received[1]["sign"],
                base64.b64encode(hmac.digest(key, b"", hashlib.sha256)).decode(),
            )
            server.shutdown()
