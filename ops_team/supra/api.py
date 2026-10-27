"""Authenticated loopback read API; business service remains the single engine."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from secrets import compare_digest
from dataclasses import dataclass, field
import threading
import json
from .domain import encode, Principal


@dataclass(repr=False)
class AccessGate:
    grants: dict = field(repr=False)

    def authenticate(self, authorization):
        if not authorization or not authorization.startswith("Bearer "):
            raise PermissionError("Cần Bearer token phiên.")
        supplied = authorization[7:]
        for token, principal in self.grants.items():
            if len(token) >= 24 and compare_digest(supplied.encode("utf-8"), token.encode("utf-8")):
                return principal
        raise PermissionError("Token không hợp lệ hoặc đã hết phiên.")


def handler_for(service, gate):
    class Handler(BaseHTTPRequestHandler):
        server_version = "SupraLocal/1"
        def log_message(self, *args):
            pass  # No URL/header/token/request logs.

        def send_json(self, status, value):
            content = encode(value).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            try:
                principal = gate.authenticate(self.headers.get("Authorization"))
                path = urlparse(self.path)
                raw = parse_qs(path.query, keep_blank_values=True)
                allowed = {"mode", "group_by", "hub", "route", "date", "vehicle_id", "store_id", "order_id"} if path.path == "/v1/supra/detail" else {"mode", "days", "hub", "route", "start", "end"}
                if any(len(v) != 1 for v in raw.values()) or not set(raw) <= allowed:
                    raise ValueError("Tham số không được hỗ trợ; scope khách hàng không lấy từ request.")
                params = {k: v[0] for k, v in raw.items()}
                if path.path == "/v1/supra/pnl":
                    result = service.query_pnl(principal, **params)
                elif path.path == "/v1/supra/detail":
                    result = service.query_detail(principal, **params)
                elif path.path == "/v1/supra/reconciliation" and not params:
                    result = service.reconciliation(principal)
                else:
                    return self.send_json(404, {"error": "Không có API này."})
                self.send_json(200, result)
            except PermissionError:
                self.send_json(403, {"error": "Không có quyền hoặc token phiên không hợp lệ."})
            except (ValueError, TypeError):
                self.send_json(400, {"error": "Đầu vào không hợp lệ; kiểm tra mode/kỳ/hub/tuyến."})

        def do_POST(self):
            self.send_json(405, {"error": "API chỉ đọc. Không gửi GTalk hoặc thực thi vận hành."})
    return Handler


class LocalAPI:
    def __init__(self, service, token, principal, port=8502):
        principal.require("VIEW_PNL")
        if len(token) < 24:
            raise ValueError("Token RAM cần ít nhất 24 ký tự.")
        self.server = ThreadingHTTPServer(("127.0.0.1", port), handler_for(service, AccessGate({token: principal})))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
