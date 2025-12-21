#!/usr/bin/env python3
import http.server
import socketserver
import os

PORT = 2125
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 优先使用 dist 目录（生产构建），否则使用源目录（开发模式）
DIST_DIR = os.path.join(SCRIPT_DIR, 'dist')
if os.path.exists(DIST_DIR) and os.path.isdir(DIST_DIR):
    DIRECTORY = DIST_DIR
    MODE = "production"
else:
    DIRECTORY = SCRIPT_DIR
    MODE = "development"

class MyHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def end_headers(self):
        # 开发模式禁用缓存，生产模式允许缓存静态资源
        if MODE == "development":
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        else:
            # 生产模式：对带 hash 的资源文件长期缓存，对 HTML 禁用缓存
            if '/assets/' in self.path:
                self.send_header('Cache-Control', 'public, max-age=31536000, immutable')
            else:
                self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

# 允许端口重用，解决 "Address already in use" 问题
class ReuseAddrTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

if __name__ == '__main__':
    with ReuseAddrTCPServer(("", PORT), MyHTTPRequestHandler) as httpd:
        print(f"前端服务运行在 http://localhost:{PORT}")
        print(f"模式: {MODE}")
        print(f"目录: {DIRECTORY}")
        print("按 Ctrl+C 停止服务")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n服务已停止")
