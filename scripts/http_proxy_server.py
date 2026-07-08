"""
Лёгкий HTTP forward proxy для запуска на рабочем ПК (Windows/Linux).

Репрайсер на VPS отправляет запросы к card.wb.ru через этот прокси,
используя IP рабочего сервера вместо забаненного IP датацентра.

Запуск:
    python http_proxy_server.py                   # порт 8899, без авторизации
    python http_proxy_server.py --port 8899 --auth user:pass

В .env на VPS:
    CATALOG_PROXY_URL=http://user:pass@<рабочий_IP>:8899

Не забудь открыть порт в Windows Firewall!
"""

import argparse
import base64
import http.server
import socket
import ssl
import threading
import urllib.parse
import urllib.request


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    auth_header = None  # set by main() if --auth is given

    def _check_auth(self):
        if not self.auth_header:
            return True
        got = self.headers.get("Proxy-Authorization", "")
        if got == self.auth_header:
            return True
        self.send_response(407)
        self.send_header("Proxy-Authenticate", 'Basic realm="proxy"')
        self.end_headers()
        return False

    def do_CONNECT(self):
        """HTTPS tunnelling (CONNECT method)."""
        if not self._check_auth():
            return
        host, port = self.path.split(":")
        port = int(port)
        try:
            remote = socket.create_connection((host, port), timeout=15)
        except Exception as e:
            self.send_error(502, str(e))
            return
        self.send_response(200, "Connection Established")
        self.end_headers()
        self.connection.setblocking(False)
        remote.setblocking(False)
        conns = [self.connection, remote]
        while True:
            import select as _sel
            readable, _, errs = _sel.select(conns, [], conns, 30)
            if errs:
                break
            if not readable:
                break
            done = False
            for s in readable:
                other = remote if s is self.connection else self.connection
                try:
                    data = s.recv(65536)
                except Exception:
                    done = True
                    break
                if not data:
                    done = True
                    break
                try:
                    other.sendall(data)
                except Exception:
                    done = True
                    break
            if done:
                break
        remote.close()

    def do_GET(self):
        self._proxy_request()

    def do_POST(self):
        self._proxy_request()

    def do_PUT(self):
        self._proxy_request()

    def do_DELETE(self):
        self._proxy_request()

    def _proxy_request(self):
        if not self._check_auth():
            return
        url = self.path
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else None

        headers = {}
        for key, val in self.headers.items():
            low = key.lower()
            if low in ("host", "proxy-authorization", "proxy-connection"):
                continue
            headers[key] = val

        try:
            req = urllib.request.Request(url, data=body, headers=headers, method=self.command)
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                self.send_response(resp.status)
                for key, val in resp.getheaders():
                    if key.lower() not in ("transfer-encoding",):
                        self.send_header(key, val)
                self.end_headers()
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except Exception as e:
            self.send_error(502, f"Proxy error: {e}")

    def log_message(self, fmt, *args):
        print(f"[PROXY] {self.client_address[0]} - {fmt % args}")


def main():
    parser = argparse.ArgumentParser(description="HTTP forward proxy for WB catalog")
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--auth", help="user:pass for basic auth")
    args = parser.parse_args()

    if args.auth:
        encoded = base64.b64encode(args.auth.encode()).decode()
        ProxyHandler.auth_header = f"Basic {encoded}"
        print(f"Auth enabled: {args.auth.split(':')[0]}:****")

    server = http.server.HTTPServer((args.bind, args.port), ProxyHandler)
    print(f"HTTP proxy listening on {args.bind}:{args.port}")
    print(f"Для .env: CATALOG_PROXY_URL=http://{args.auth + '@' if args.auth else ''}<ВАШ_IP>:{args.port}")
    print("Ctrl+C для остановки")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nProxy stopped.")
        server.shutdown()


if __name__ == "__main__":
    main()
