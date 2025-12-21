#!/usr/bin/env python3
import http.server
import socketserver
import os
import urllib.request
import urllib.error

PORT = 2125
BACKEND_URL = 'http://localhost:2124'
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

    def do_GET(self):
        if self.path.startswith('/api'):
            self.proxy_request('GET')
        else:
            super().do_GET()

    def do_POST(self):
        if self.path.startswith('/api'):
            self.proxy_request('POST')
        else:
            self.send_error(405, 'Method Not Allowed')

    def do_PUT(self):
        if self.path.startswith('/api'):
            self.proxy_request('PUT')
        else:
            self.send_error(405, 'Method Not Allowed')

    def do_DELETE(self):
        if self.path.startswith('/api'):
            self.proxy_request('DELETE')
        else:
            self.send_error(405, 'Method Not Allowed')

    def proxy_request(self, method):
        """代理 API 请求到后端服务"""
        target_url = BACKEND_URL + self.path

        # 读取请求体
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length > 0 else None

        # 构建代理请求
        req = urllib.request.Request(target_url, data=body, method=method)

        # 复制相关请求头
        for header in ['Content-Type', 'Cookie', 'Authorization', 'X-API-Key']:
            if header in self.headers:
                req.add_header(header, self.headers[header])

        try:
            with urllib.request.urlopen(req) as response:
                # 发送响应状态
                self.send_response(response.status)
                # 复制响应头
                for header, value in response.getheaders():
                    if header.lower() not in ['transfer-encoding', 'connection']:
                        self.send_header(header, value)
                self.end_headers()
                # 发送响应体
                self.wfile.write(response.read())
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            for header, value in e.headers.items():
                if header.lower() not in ['transfer-encoding', 'connection']:
                    self.send_header(header, value)
            self.end_headers()
            self.wfile.write(e.read())
        except urllib.error.URLError as e:
            self.send_error(502, f'Backend unavailable: {e.reason}')

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
