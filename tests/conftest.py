import socket
import threading
import http.server
import pytest
from pathlib import Path

DOCS_DIR = Path(__file__).parent.parent / "docs"


def _free_port():
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def static_server():
    """Serves docs/ over HTTP for Playwright tests."""
    port = _free_port()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(DOCS_DIR), **kwargs)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("localhost", port), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://localhost:{port}"
    server.shutdown()
