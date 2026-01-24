"""
E2E test fixtures: starts real backend + frontend servers with a temporary database.
"""
import pytest
import os
import sys
import tempfile
import subprocess
import time
import socket


def find_free_port():
    """Find a free TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]


def wait_for_server(url, timeout=15):
    """Wait for a server to respond."""
    import urllib.request
    for _ in range(timeout * 10):
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.1)
    return False


@pytest.fixture(scope="session")
def server_url():
    """
    Start backend (uvicorn) and frontend (server.py) with a temporary database.
    Returns the frontend URL (which proxies API calls to the backend).
    """
    backend_port = find_free_port()
    frontend_port = find_free_port()
    fd, db_path = tempfile.mkstemp(suffix='.db')
    os.close(fd)

    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    backend_dir = os.path.join(project_root, 'backend')
    frontend_dir = os.path.join(project_root, 'frontend')
    # Use built dist/ if available (required for Tailwind CSS compilation)
    frontend_serve_dir = os.path.join(frontend_dir, 'dist')
    if not os.path.isdir(frontend_serve_dir):
        frontend_serve_dir = frontend_dir

    env = os.environ.copy()
    env['DATABASE_PATH'] = db_path
    env['INIT_MOCK_DATA'] = '0'
    env['ENABLE_AUDIT_LOG'] = '0'
    env['DISABLE_RATE_LIMIT'] = '1'

    # Start backend (uvicorn)
    backend_proc = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'app:app',
         '--host', '127.0.0.1', '--port', str(backend_port)],
        cwd=backend_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    backend_url = f"http://127.0.0.1:{backend_port}"
    if not wait_for_server(f"{backend_url}/api/auth/status"):
        backend_proc.terminate()
        backend_proc.wait()
        os.unlink(db_path)
        pytest.fail("Backend server failed to start")

    # Start frontend server (proxies /api to backend)
    frontend_env = env.copy()
    frontend_env['BACKEND_URL'] = backend_url
    frontend_proc = subprocess.Popen(
        [sys.executable, 'server.py'],
        cwd=frontend_dir,
        env=frontend_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Monkey-patch the frontend server port
    # The server.py uses PORT=2125 by default, but we want a random port.
    # Since we can't easily pass port to server.py, let's use the backend directly
    # and navigate Playwright to serve the frontend index.html via the backend.
    # Actually, let's just override: kill frontend_proc and use a simpler approach.
    frontend_proc.terminate()
    frontend_proc.wait()

    # Use a simple HTTP server for the frontend directory
    frontend_proc = subprocess.Popen(
        [sys.executable, '-m', 'http.server', str(frontend_port), '--directory', frontend_dir],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    frontend_url = f"http://127.0.0.1:{frontend_port}"
    if not wait_for_server(frontend_url):
        backend_proc.terminate()
        frontend_proc.terminate()
        backend_proc.wait()
        frontend_proc.wait()
        os.unlink(db_path)
        pytest.fail("Frontend server failed to start")

    # The frontend page needs to call the backend API.
    # Since it's served from a different port, we need to configure the API base URL.
    # The frontend uses relative '/api' paths, so we'll use the backend URL directly.
    # For simplicity in E2E tests, just use the backend URL + serve frontend from backend.
    # Actually, the simplest approach: just test against the backend directly for API,
    # and for the UI test, use a page that includes the correct script.
    #
    # Best approach: Use the backend URL directly since all the HTML/JS/CSS are accessible.
    # The index.html uses relative paths ('./src/main.css'), so we need the frontend server.
    # But API calls use '/api' which won't reach the backend from the frontend server.
    #
    # Final approach: Use the backend for both (mount frontend as static).
    # Since the app doesn't do this, let's configure the frontend API base.
    # The frontend JS likely uses fetch('/api/...') which is relative to current host.
    # So we need a server that serves both static files AND proxies /api.
    # That's exactly what frontend/server.py does!

    # Kill the simple HTTP server and use server.py with the correct port
    frontend_proc.terminate()
    frontend_proc.wait()

    # Modify server.py's PORT via a wrapper script
    wrapper_code = f"""
import sys, os
sys.path.insert(0, '{frontend_dir}')
os.chdir('{frontend_dir}')

import http.server
import socketserver
import urllib.request
import urllib.error

PORT = {frontend_port}
BACKEND_URL = '{backend_url}'
DIRECTORY = '{frontend_serve_dir}'

class ProxyHandler(http.server.SimpleHTTPRequestHandler):
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
            self.send_error(405)

    def do_PUT(self):
        if self.path.startswith('/api'):
            self.proxy_request('PUT')
        else:
            self.send_error(405)

    def do_DELETE(self):
        if self.path.startswith('/api'):
            self.proxy_request('DELETE')
        else:
            self.send_error(405)

    def proxy_request(self, method):
        target_url = BACKEND_URL + self.path
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length > 0 else None
        req = urllib.request.Request(target_url, data=body, method=method)
        for header in ['Content-Type', 'Cookie', 'Authorization', 'X-API-Key']:
            if header in self.headers:
                req.add_header(header, self.headers[header])
        try:
            with urllib.request.urlopen(req) as response:
                self.send_response(response.status)
                for header, value in response.getheaders():
                    if header.lower() not in ['transfer-encoding', 'connection']:
                        self.send_header(header, value)
                self.end_headers()
                self.wfile.write(response.read())
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            for header, value in e.headers.items():
                if header.lower() not in ['transfer-encoding', 'connection']:
                    self.send_header(header, value)
            self.end_headers()
            self.wfile.write(e.read())
        except urllib.error.URLError as e:
            self.send_error(502, f'Backend unavailable: {{e.reason}}')

    def log_message(self, format, *args):
        pass  # Suppress logs

class ReuseAddrServer(socketserver.TCPServer):
    allow_reuse_address = True

with ReuseAddrServer(("127.0.0.1", PORT), ProxyHandler) as httpd:
    httpd.serve_forever()
"""
    fd_wrapper, wrapper_path = tempfile.mkstemp(suffix='.py')
    os.write(fd_wrapper, wrapper_code.encode())
    os.close(fd_wrapper)

    frontend_proc = subprocess.Popen(
        [sys.executable, wrapper_path],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    if not wait_for_server(f"{frontend_url}/index.html"):
        backend_proc.terminate()
        frontend_proc.terminate()
        backend_proc.wait()
        frontend_proc.wait()
        os.unlink(db_path)
        os.unlink(wrapper_path)
        pytest.fail("Frontend proxy server failed to start")

    yield frontend_url

    # Cleanup
    frontend_proc.terminate()
    backend_proc.terminate()
    try:
        frontend_proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        frontend_proc.kill()
    try:
        backend_proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        backend_proc.kill()
    try:
        os.unlink(db_path)
    except OSError:
        pass
    try:
        os.unlink(wrapper_path)
    except OSError:
        pass


@pytest.fixture(scope="session")
def setup_admin(server_url):
    """Setup admin user on the test server."""
    import urllib.request
    import json

    data = json.dumps({
        "username": "admin",
        "password": "Admin123!",
        "display_name": "E2E Admin"
    }).encode()

    req = urllib.request.Request(
        f"{server_url}/api/auth/setup",
        data=data,
        headers={'Content-Type': 'application/json'}
    )
    resp = urllib.request.urlopen(req)
    assert resp.status == 200
    return {"username": "admin", "password": "Admin123!"}
