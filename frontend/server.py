#!/usr/bin/env python3
import http.server
import socketserver
import os

PORT = 2125
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

class MyHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        super().end_headers()

# 允许端口重用，解决 "Address already in use" 问题
class ReuseAddrTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

if __name__ == '__main__':
    with ReuseAddrTCPServer(("", PORT), MyHTTPRequestHandler) as httpd:
        print(f"前端服务运行在 http://localhost:{PORT}")
        print("按 Ctrl+C 停止服务")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n服务已停止")
