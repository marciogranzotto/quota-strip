"""Short-lived loopback OAuth callback, using the native-app PKCE pattern."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import queue
import threading
from urllib.parse import parse_qs, urlsplit

from quota_api import QuotaError


class LoopbackCallback:
    def __init__(self, state):
        self.state = state
        self.result = queue.Queue(maxsize=1)
        self.accepted = False
        self.lock = threading.Lock()
        callback = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def log_message(self, *_):
                pass  # Request URLs contain short-lived authorization codes.

            def do_GET(self):
                url = urlsplit(self.path)
                query = parse_qs(url.query)
                valid = (self.headers.get('Host') == f'localhost:{self.server.server_port}'
                         and url.path == '/callback'
                         and len(query.get('state', [])) == 1
                         and hmac.compare_digest(query['state'][0].encode(), callback.state.encode()))
                code = query.get('code', [])
                error = query.get('error', [])
                if not valid or not ((len(code) == 1 and not error) or
                                     (len(error) == 1 and not code)):
                    self.respond(400, 'Invalid callback. Return to the sign-in page.')
                    return
                with callback.lock:
                    if callback.accepted:
                        self.respond(409, 'This sign-in callback was already received.')
                        return
                    callback.accepted = True
                    callback.result.put_nowait(None if error else code[0])
                self.respond(200, 'Sign-in response received. You can close this tab and return to Quota Strip.')

            def respond(self, status, text):
                body = text.encode()
                self.send_response(status)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('Content-Security-Policy', "default-src 'none'")
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def redirect_uri(self):
        return f'http://localhost:{self.server.server_port}/callback'

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def wait(self, timeout=900):
        try:
            code = self.result.get(timeout=timeout)
        except queue.Empty:
            raise QuotaError('Browser sign-in timed out; start again') from None
        if code is None:
            raise QuotaError('Browser sign-in was declined')
        return code
