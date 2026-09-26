def read_request(sock, buf):
    # Read until the end of the headers.
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("closed before headers ended")
        buf += chunk
    head, buf = buf.split(b"\r\n\r\n", 1)

    # Find Content-Length (missing means 0).
    length = 0
    for line in head.split(b"\r\n")[1:]:
        name, _, value = line.partition(b":")
        if name.strip().lower() == b"content-length":
            length = int(value)

    # Read until the body is complete.
    while len(buf) < length:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("closed before body ended")
        buf += chunk

    return head, buf[:length], buf[length:]


class FakeSock:
    def __init__(self, chunks): self.chunks = list(chunks)
    def recv(self, n): return self.chunks.pop(0) if self.chunks else b""


def test_post_split_across_three_chunks():
    sock = FakeSock([
        b"POST /x HTTP/1.1\r\nHost: a\r\nContent-Le",   # split inside headers
        b"ngth: 11\r\n\r\nhello",                       # split inside body
        b" world",
    ])
    head, body, leftover = read_request(sock, b"")
    assert head == b"POST /x HTTP/1.1\r\nHost: a\r\nContent-Length: 11"
    assert body == b"hello world"
    assert leftover == b""


def test_two_requests_in_one_chunk():
    sock = FakeSock([
        b"POST /a HTTP/1.1\r\nContent-Length: 3\r\n\r\nabc"
        b"GET /b HTTP/1.1\r\nHost: a\r\n\r\n"
    ])
    head1, body1, leftover = read_request(sock, b"")
    assert head1 == b"POST /a HTTP/1.1\r\nContent-Length: 3"
    assert body1 == b"abc"

    head2, body2, leftover = read_request(sock, leftover)
    assert head2 == b"GET /b HTTP/1.1\r\nHost: a"
    assert body2 == b""
    assert leftover == b""
