from dataclasses import dataclass

MAX_REQUEST_LINE = 4094
MAX_FIELDS = 100
MAX_CHUNK_SIZE_DIGITS = 16
MAX_HEADER_BYTES = 8192
MAX_CHUNK_LINE = 4096
MAX_BODY = 1 << 20

TCHAR = frozenset(
    b"!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
)
HEXDIG = frozenset(b"0123456789abcdefABCDEF")
FORBIDDEN_TRAILERS = frozenset({"content-length", "transfer-encoding", "host"})

Fields = tuple[tuple[str, str], ...]


class NeedMoreData(Exception):
    """The buffer holds an incomplete request; read more bytes and retry."""


class BadRequest(Exception):
    def __init__(self, status: int, reason: str) -> None:
        super().__init__(f"{status} {reason}")
        self.status = status
        self.reason = reason


@dataclass(frozen=True)
class Request:
    method: str
    target: str
    version: str
    headers: Fields
    body: bytes = b""
    trailers: Fields = ()

    def get(self, name: str) -> str | None:
        name = name.lower()
        for key, value in self.headers:
            if key.lower() == name:
                return value
        return None


def parse_request(buf: bytes) -> tuple[Request, bytes]:
    start = 2 if buf.startswith(b"\r\n") else 0
    line, pos = _read_line(buf, start, MAX_REQUEST_LINE, 414)
    method, target, version = _parse_request_line(line)
    headers, pos = _parse_fields(buf, pos)

    if version == "HTTP/1.1" and len(_values(headers, "host")) != 1:
        raise BadRequest(400, "HTTP/1.1 requires exactly one Host header")

    trailers: Fields = ()
    if _is_chunked(headers, version):
        body, trailers, pos = _read_chunked(buf, pos)
    else:
        length = _content_length(headers)
        if len(buf) - pos < length:
            raise NeedMoreData
        body, pos = buf[pos : pos + length], pos + length

    request = Request(method, target, version, headers, body, trailers)
    return request, buf[pos:]


def _read_line(buf: bytes, pos: int, max_len: int, status: int) -> tuple[bytes, int]:
    end = buf.find(b"\n", pos, pos + max_len + 2)
    if end == -1:
        # No LF yet. Give up only once max_len bytes plus a CR are buffered.
        if len(buf) - pos >= max_len + 2:
            raise BadRequest(status, "line too long")
        raise NeedMoreData
    if end == pos or buf[end - 1] != ord("\r"):
        raise BadRequest(400, "line not terminated by CRLF")
    return buf[pos : end - 1], end + 1


def _parse_request_line(line: bytes) -> tuple[str, str, str]:
    parts = line.split(b" ")
    if len(parts) != 3:
        raise BadRequest(400, "request-line must have exactly two spaces")
    method, target, version = parts
    if not _is_token(method):
        raise BadRequest(400, "invalid method")
    if not target or any(c <= 0x20 or c == 0x7F for c in target):
        raise BadRequest(400, "invalid request-target")
    if version not in (b"HTTP/1.0", b"HTTP/1.1"):
        raise BadRequest(400, "unsupported HTTP version")
    return method.decode("ascii"), target.decode("latin-1"), version.decode("ascii")


def _parse_fields(buf: bytes, pos: int) -> tuple[Fields, int]:
    """Parse field lines up to and including the empty line that ends them."""
    limit = pos + MAX_HEADER_BYTES
    fields: list[tuple[str, str]] = []
    while True:
        line, pos = _read_line(buf, pos, limit - pos - 2, 431)
        if not line:
            return tuple(fields), pos
        if len(fields) == MAX_FIELDS:
            raise BadRequest(431, "too many header fields")
        if line[0] in b" \t":
            raise BadRequest(400, "obs-fold is not allowed")
        name, colon, value = line.partition(b":")
        if not colon or not _is_token(name):
            raise BadRequest(400, "invalid field name")
        value = value.strip(b" \t")
        if any(c in b"\r\n\0" for c in value):
            raise BadRequest(400, "invalid character in field value")
        fields.append((name.decode("ascii"), value.decode("latin-1")))


def _is_chunked(headers: Fields, version: str) -> bool:
    """Apply RFC 9112 §6.3: decide whether the body is chunked."""
    te = _values(headers, "transfer-encoding")
    if not te:
        return False
    if version == "HTTP/1.0":
        raise BadRequest(400, "Transfer-Encoding in an HTTP/1.0 request")
    if _values(headers, "content-length"):
        raise BadRequest(400, "both Content-Length and Transfer-Encoding")
    codings = [c.lower() for c in _split_list(te)]
    if codings[-1] != "chunked":
        raise BadRequest(400, "chunked must be the final transfer coding")
    if codings.count("chunked") > 1:
        raise BadRequest(400, "chunked applied more than once")
    if len(codings) > 1:
        raise BadRequest(501, "unsupported transfer coding")
    return True


def _content_length(headers: Fields) -> int:
    values = _split_list(_values(headers, "content-length"))
    if not values:
        return 0
    if not all(v.isascii() and v.isdigit() for v in values):
        raise BadRequest(400, "Content-Length must be digits only")
    if len(set(values)) != 1:
        raise BadRequest(400, "conflicting Content-Length values")
    length = int(values[0])
    if length > MAX_BODY:
        raise BadRequest(413, "body too large")
    return length


def _read_chunked(buf: bytes, pos: int) -> tuple[bytes, Fields, int]:
    body = bytearray()
    while True:
        line, pos = _read_line(buf, pos, MAX_CHUNK_LINE, 400)
        size_hex = line.split(b";", 1)[0]  # chunk extensions are ignored
        if not (0 < len(size_hex) <= MAX_CHUNK_SIZE_DIGITS) or set(size_hex) - HEXDIG:
            raise BadRequest(400, "invalid chunk size")
        size = int(size_hex, 16)
        if size == 0:
            trailers, pos = _parse_fields(buf, pos)
            if any(name.lower() in FORBIDDEN_TRAILERS for name, _ in trailers):
                raise BadRequest(400, "framing field in trailers")
            return bytes(body), trailers, pos
        if len(body) + size > MAX_BODY:
            raise BadRequest(413, "body too large")
        end = pos + size
        if len(buf) < end + 2:
            raise NeedMoreData
        if buf[end : end + 2] != b"\r\n":
            raise BadRequest(400, "chunk data not followed by CRLF")
        body += buf[pos:end]
        pos = end + 2


def _is_token(value: bytes) -> bool:
    return bool(value) and set(value) <= TCHAR


def _values(fields: Fields, name: str) -> list[str]:
    return [v for k, v in fields if k.lower() == name]


def _split_list(values: list[str]) -> list[str]:
    return [item.strip(" \t") for v in values for item in v.split(",")]
