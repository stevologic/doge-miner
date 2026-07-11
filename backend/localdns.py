"""doge.local — zero-config local DNS for the miner UI.

While the server runs, it announces the hostname `doge.local` over mDNS
(the protocol behind Bonjour/Avahi), so the local machine — and any device
on the same network — can open the dashboard at http://doge.local:8000
without touching the hosts file or router. Windows 10+/macOS resolve
`.local` names natively; Linux needs avahi + nss-mdns (standard on desktop
distros).

Also starts a best-effort redirector on port 80 so plain http://doge.local
works in a browser (skipped silently if port 80 is taken or privileged).

Everything here is optional and fail-soft: no zeroconf package, no port 80,
no network — the miner itself is unaffected. Disable with DOGE_NO_MDNS=1.
"""

import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

MDNS_HOSTNAME = "doge.local"


def get_lan_ip() -> str:
    """Primary outbound IPv4 (no packets are actually sent); falls back to loopback."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return "127.0.0.1"


class LocalDns:
    """Registers doge.local over mDNS and (best-effort) redirects :80 -> :port."""

    def __init__(self, port: int = 8000):
        self.port = port
        self.ip = get_lan_ip()
        self.zeroconf = None
        self.service_info = None
        self.redirect_server = None
        self.active = False
        self.error = ""

    def start(self) -> bool:
        if os.environ.get("DOGE_NO_MDNS"):
            self.error = "disabled via DOGE_NO_MDNS"
            return False
        try:
            from zeroconf import Zeroconf, ServiceInfo
        except ImportError:
            self.error = "python package 'zeroconf' not installed"
            return False
        try:
            self.service_info = ServiceInfo(
                "_http._tcp.local.",
                "DOGE MINER._http._tcp.local.",
                addresses=[socket.inet_aton(self.ip)],
                port=self.port,
                server=f"{MDNS_HOSTNAME}.",
                properties={"path": "/"},
            )
            self.zeroconf = Zeroconf()
            self.zeroconf.register_service(self.service_info)
            self.active = True
        except Exception as e:  # never let name advertising break the miner
            self.error = f"{e.__class__.__name__}: {e}"
            self.zeroconf = None
            return False
        self._start_port80_redirect()
        return True

    def _start_port80_redirect(self):
        """Plain http://doge.local (implicit :80) -> the real UI port. Best effort."""
        target = f"http://{MDNS_HOSTNAME}:{self.port}"

        class Redirect(BaseHTTPRequestHandler):
            def _go(self):
                self.send_response(302)
                self.send_header("Location", target + self.path)
                self.end_headers()

            do_GET = do_HEAD = do_POST = _go

            def log_message(self, *args):  # keep server stdout clean
                pass

        try:
            self.redirect_server = ThreadingHTTPServer(("0.0.0.0", 80), Redirect)
        except OSError:
            self.redirect_server = None  # port taken/privileged: doge.local:8000 still works
            return
        t = threading.Thread(target=self.redirect_server.serve_forever, daemon=True)
        t.start()

    def stop(self):
        if self.redirect_server is not None:
            try:
                self.redirect_server.shutdown()
            except Exception:
                pass
            self.redirect_server = None
        if self.zeroconf is not None:
            try:
                if self.service_info is not None:
                    self.zeroconf.unregister_service(self.service_info)
                self.zeroconf.close()
            except Exception:
                pass
            self.zeroconf = None
        self.active = False


_instance: Optional[LocalDns] = None


def start(port: int = 8000) -> LocalDns:
    global _instance
    if _instance is None:
        _instance = LocalDns(port)
        _instance.start()
    return _instance


def stop():
    global _instance
    if _instance is not None:
        _instance.stop()
        _instance = None
