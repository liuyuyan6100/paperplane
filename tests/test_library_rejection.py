"""Rejected uploads return their HTTP error without processing or waiting forever."""
import socket
import threading
import urllib.error
import urllib.request
from unittest.mock import Mock

import pytest

from library.server import Handler, Server


@pytest.fixture
def endpoint():
    library = Mock()
    server = Server(('127.0.0.1', 0), library)
    finished = threading.Event()
    class TrackedHandler(Handler):
        def finish(self):
            try:
                super().finish()
            finally:
                finished.set()
    server.RequestHandlerClass = TrackedHandler
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, library, finished
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize('guard', ['token', 'origin', 'host'])
def test_large_rejected_upload_returns_403_without_touching_library(endpoint, guard):
    server, library, _ = endpoint
    headers = {'Content-Type': 'application/json'}
    if guard == 'origin':
        headers.update({'Origin': 'https://synthetic.invalid', 'X-Library-Token': server.token})
    elif guard == 'host':
        headers['Host'] = 'synthetic.invalid'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(10):
        request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/upload',
                                         data=b'x' * 1048576, headers=headers)
        with pytest.raises(urllib.error.HTTPError) as caught:
            opener.open(request, timeout=3)
        with caught.value as response:
            assert response.code == 403
            assert response.headers['Connection'] == 'close'
            assert response.read()
    assert library.mock_calls == []


def test_unfinished_rejected_body_has_bounded_handler_lifetime(endpoint):
    server, library, finished = endpoint
    with socket.create_connection(('127.0.0.1', server.server_port), timeout=3) as client:
        client.sendall((f'POST /api/upload HTTP/1.1\r\nHost: 127.0.0.1:{server.server_port}\r\n'
                        'Content-Type: application/json\r\nContent-Length: 1048576\r\n\r\n').encode())
        # The denial is sent before waiting for a body that may never arrive.
        assert b' 403 ' in client.recv(4096)
        assert finished.wait(3), 'Rejected slow request must release the handler'
    assert library.mock_calls == []


def test_rejected_body_drain_obeys_size_ceiling(monkeypatch):
    monkeypatch.setattr('library.server.MAX_FILE', 9)
    handler = object.__new__(Handler)
    handler.headers = {'Content-Length': '999999999999999'}
    handler.connection = Mock()
    handler.rfile = Mock()
    handler.rfile.read1.side_effect = lambda size: b'x' * size
    handler.discard_rejected_body()
    handler.rfile.read1.assert_called_once_with(10012)
    assert 0 < handler.connection.settimeout.call_args.args[0] <= 1


@pytest.mark.parametrize('length', ['-1', 'invalid', '0'])
def test_invalid_or_empty_rejected_length_does_not_read(length):
    handler = object.__new__(Handler)
    handler.headers = {'Content-Length': length}
    handler.rfile = Mock()
    handler.connection = Mock()
    handler.discard_rejected_body()
    handler.rfile.read1.assert_not_called()
