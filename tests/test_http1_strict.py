"""Tests for socket_server.http1_strict.

Sections: R1-R6 are the spec rules, F1-F7 the review findings, then the
16 required cases and the incremental feeding tests.
"""

import dataclasses

import pytest

from socket_server.http1_strict import (
    MAX_BODY,
    MAX_HEADER_BYTES,
    BadRequest,
    NeedMoreData,
    parse_request,
)


def req(*lines: str, body: bytes = b"") -> bytes:
    """Build a request: CRLF-terminated lines, a blank line, then the body."""
    return "".join(line + "\r\n" for line in lines).encode("latin-1") + b"\r\n" + body


def post(*fields: str, body: bytes = b"") -> bytes:
    return req("POST / HTTP/1.1", "Host: a", *fields, body=body)


def chunked(body: bytes) -> bytes:
    return post("Transfer-Encoding: chunked", body=body)


def assert_status(buf: bytes, status: int) -> None:
    with pytest.raises(BadRequest) as exc:
        parse_request(buf)
    assert exc.value.status == status


# --- Request and API ---------------------------------------------------------


def test_simple_get() -> None:
    request, rest = parse_request(req("GET /index.html HTTP/1.1", "Host: a.test"))
    assert request.method == "GET"
    assert request.target == "/index.html"
    assert request.version == "HTTP/1.1"
    assert request.headers == (("Host", "a.test"),)
    assert request.body == b""
    assert request.trailers == ()
    assert rest == b""


def test_headers_keep_original_order_and_get_is_case_insensitive() -> None:
    request, _ = parse_request(req("GET / HTTP/1.1", "X-B: 2", "Host: a", "X-A: 1"))
    assert request.headers == (("X-B", "2"), ("Host", "a"), ("X-A", "1"))
    assert request.get("host") == "a"
    assert request.get("X-A") == "1"
    assert request.get("missing") is None


def test_request_attributes_cannot_be_rebound() -> None:
    request, _ = parse_request(req("GET / HTTP/1.1", "Host: a"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.method = "POST"  # type: ignore[misc]


@pytest.mark.parametrize(
    "buf",
    [
        b"",
        b"GET / HTTP/1.1",
        b"GET / HTTP/1.1\r",
        b"GET / HTTP/1.1\r\nHost: a\r\n",
        b"GET / HTTP/1.1\r\nHost: a\r\n\r",
    ],
)
def test_incomplete_head_needs_more_data(buf: bytes) -> None:
    with pytest.raises(NeedMoreData):
        parse_request(buf)


# --- R1: request-line --------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "GET  / HTTP/1.1",
        "GET / HTTP/1.1 ",
        " GET / HTTP/1.1",
        "GET\t/ HTTP/1.1",
        "GET /",
        "GET / HTTP/1.1 extra",
    ],
)
def test_r1_request_line_needs_exactly_two_sp(line: str) -> None:
    assert_status(req(line, "Host: a"), 400)


@pytest.mark.parametrize("method", ["G(T", "GE:T", "GET@"])
def test_r1_method_must_be_token(method: str) -> None:
    assert_status(req(f"{method} / HTTP/1.1", "Host: a"), 400)


def test_r1_method_is_case_sensitive() -> None:
    request, _ = parse_request(req("get / HTTP/1.1", "Host: a"))
    assert request.method == "get"


@pytest.mark.parametrize("version", ["HTTP/2.0", "HTTP/1.2", "http/1.1", "HTTP/1"])
def test_r1_version_must_be_1_0_or_1_1(version: str) -> None:
    assert_status(req(f"GET / {version}", "Host: a"), 400)


def test_r1_http_1_0_is_accepted() -> None:
    request, _ = parse_request(req("GET / HTTP/1.0"))
    assert request.version == "HTTP/1.0"


def test_r1_request_line_of_4094_bytes_is_accepted() -> None:
    line = "GET /" + "a" * (4094 - len("GET / HTTP/1.1")) + " HTTP/1.1"
    assert len(line) == 4094
    parse_request(req(line, "Host: a"))


def test_r1_request_line_of_4095_bytes_gives_414() -> None:
    line = "GET /" + "a" * (4095 - len("GET / HTTP/1.1")) + " HTTP/1.1"
    assert_status(req(line, "Host: a"), 414)


def test_r1_unterminated_long_request_line_gives_414() -> None:
    assert_status(b"GET /" + b"a" * 5000, 414)


# --- R2: CRLF line endings ---------------------------------------------------


@pytest.mark.parametrize(
    "buf",
    [
        b"GET / HTTP/1.1\nHost: a\r\n\r\n",
        b"GET / HTTP/1.1\r\nHost: a\n\r\n",
        b"GET / HTTP/1.1\r\nHost: a\r\n\n",
    ],
)
def test_r2_bare_lf_gives_400(buf: bytes) -> None:
    assert_status(buf, 400)


# --- R3: header fields -------------------------------------------------------


@pytest.mark.parametrize("field", ["X(Y: 1", "X Y: 1", ": 1", "NoColon"])
def test_r3_field_name_must_be_token(field: str) -> None:
    assert_status(req("GET / HTTP/1.1", "Host: a", field), 400)


@pytest.mark.parametrize("field", ["Host : a", "Host\t: a"])
def test_r3_whitespace_before_colon_gives_400(field: str) -> None:
    assert_status(req("GET / HTTP/1.1", field), 400)


@pytest.mark.parametrize("fold", [" continued", "\tcontinued"])
def test_r3_obs_fold_gives_400(fold: str) -> None:
    assert_status(req("GET / HTTP/1.1", "Host: a", "X: start", fold), 400)


@pytest.mark.parametrize("field", ["X:v", "X:  v  ", "X:\tv\t", "X: \t v \t "])
def test_r3_ows_around_value_is_trimmed(field: str) -> None:
    request, _ = parse_request(req("GET / HTTP/1.1", "Host: a", field))
    assert request.get("X") == "v"


def test_r3_inner_whitespace_in_value_is_kept() -> None:
    request, _ = parse_request(req("GET / HTTP/1.1", "Host: a", "X:  a  b  "))
    assert request.get("X") == "a  b"


@pytest.mark.parametrize("value", [b"a\rb", b"a\x00b", b"a\nb"])
def test_r3_cr_lf_or_nul_in_value_gives_400(value: bytes) -> None:
    assert_status(b"GET / HTTP/1.1\r\nHost: a\r\nX: " + value + b"\r\n\r\n", 400)


# --- R4: Host and field count ------------------------------------------------


@pytest.mark.parametrize("fields", [[], ["Host: a", "Host: b"], ["Host: a", "host: a"]])
def test_r4_http_1_1_needs_exactly_one_host(fields: list[str]) -> None:
    assert_status(req("GET / HTTP/1.1", *fields), 400)


def test_r4_100_fields_is_accepted() -> None:
    fields = ["Host: a"] + [f"X-{i}: v" for i in range(99)]
    request, _ = parse_request(req("GET / HTTP/1.1", *fields))
    assert len(request.headers) == 100


def test_r4_101_fields_gives_431() -> None:
    fields = ["Host: a"] + [f"X-{i}: v" for i in range(100)]
    assert_status(req("GET / HTTP/1.1", *fields), 431)


# --- R5: body framing (RFC 9112 §6.3) ----------------------------------------


@pytest.mark.parametrize(
    "te", ["Transfer-Encoding: chunked", "transfer-encoding: CHUNKED"]
)
def test_r5_te_with_chunked_last_is_chunked(te: str) -> None:
    request, rest = parse_request(post(te, body=b"3\r\nabc\r\n0\r\n\r\nNEXT"))
    assert (request.body, rest) == (b"abc", b"NEXT")


def test_r5_cl_and_te_together_gives_400() -> None:
    buf = post("Content-Length: 3", "Transfer-Encoding: chunked", body=b"0\r\n\r\n")
    assert_status(buf, 400)


@pytest.mark.parametrize("te", ["gzip", "chunked, gzip", "chunked,", ""], ids=repr)
def test_r5_te_without_chunked_last_gives_400(te: str) -> None:
    assert_status(post(f"Transfer-Encoding: {te}", body=b"0\r\n\r\n"), 400)


def test_r5_content_length_body_and_leftover() -> None:
    request, rest = parse_request(post("Content-Length: 5", body=b"helloEXTRA"))
    assert (request.body, rest) == (b"hello", b"EXTRA")


def test_r5_content_length_waits_for_full_body() -> None:
    with pytest.raises(NeedMoreData):
        parse_request(post("Content-Length: 5", body=b"hel"))


@pytest.mark.parametrize("value", ["+5", "-5", "5.0", "0x5", "5 5", "", "²", "five"])
def test_r5_content_length_must_be_ascii_digits(value: str) -> None:
    assert_status(post(f"Content-Length: {value}", body=b"hello"), 400)


@pytest.mark.parametrize(
    "fields",
    [
        ["Content-Length: 5", "Content-Length: 5"],
        ["Content-Length: 5, 5"],
        ["Content-Length: 5,5", "content-length: 5"],
    ],
)
def test_r5_identical_duplicate_content_length_is_allowed(fields: list[str]) -> None:
    assert parse_request(post(*fields, body=b"hello"))[0].body == b"hello"


@pytest.mark.parametrize(
    "fields",
    [
        ["Content-Length: 5", "Content-Length: 6"],
        ["Content-Length: 5, 6"],
        ["Content-Length: 5,"],
    ],
)
def test_r5_differing_content_length_gives_400(fields: list[str]) -> None:
    assert_status(post(*fields, body=b"hello!"), 400)


def test_r5_no_framing_header_means_empty_body() -> None:
    request, rest = parse_request(post(body=b"abc"))
    assert (request.body, rest) == (b"", b"abc")


# --- R6: chunked decoding ----------------------------------------------------


def test_r6_chunks_are_joined() -> None:
    body = b"5\r\nhello\r\n1\r\n \r\n5\r\nworld\r\n0\r\n\r\n"
    assert parse_request(chunked(body))[0].body == b"hello world"


@pytest.mark.parametrize("size", [b"a", b"A", b"0a"])
def test_r6_hex_sizes_in_either_case(size: bytes) -> None:
    body = size + b"\r\n" + b"x" * 10 + b"\r\n0\r\n\r\n"
    assert parse_request(chunked(body))[0].body == b"x" * 10


@pytest.mark.parametrize("size", [b"", b"g", b"+5", b"0x5", b" 5", b"5 ", b"-1"])
def test_r6_chunk_size_must_be_hexdig(size: bytes) -> None:
    assert_status(chunked(size + b"\r\nhello\r\n0\r\n\r\n"), 400)


def test_r6_chunk_size_of_16_digits_is_allowed() -> None:
    body = b"0000000000000003\r\nabc\r\n0\r\n\r\n"
    assert parse_request(chunked(body))[0].body == b"abc"


def test_r6_chunk_size_of_17_digits_gives_400() -> None:
    assert_status(chunked(b"00000000000000003\r\nabc\r\n0\r\n\r\n"), 400)


def test_r6_chunk_extensions_are_ignored() -> None:
    body = b"3;name=value\r\nabc\r\n0;last\r\n\r\n"
    assert parse_request(chunked(body))[0].body == b"abc"


@pytest.mark.parametrize("after", [b"XY", b"\n0", b"\r0"])
def test_r6_crlf_after_chunk_data_is_checked(after: bytes) -> None:
    assert_status(chunked(b"3\r\nabc" + after + b"\r\n0\r\n\r\n"), 400)


def test_r6_trailers_are_returned_separately() -> None:
    body = b"3\r\nabc\r\n0\r\nChecksum:  xyz \r\nX-Done: 1\r\n\r\nNEXT"
    request, rest = parse_request(chunked(body))
    assert request.trailers == (("Checksum", "xyz"), ("X-Done", "1"))
    assert request.get("Checksum") is None
    assert rest == b"NEXT"


@pytest.mark.parametrize(
    "trailer", [b"Bad Name: 1", b"X : 1", b"X: a\x00b", b" folded", b"X: 1\n"]
)
def test_r6_trailers_follow_field_rules(trailer: bytes) -> None:
    assert_status(chunked(b"0\r\n" + trailer + b"\r\n\r\n"), 400)


# --- F1: Transfer-Encoding in an HTTP/1.0 request ----------------------------


def test_f1_transfer_encoding_in_http_1_0_gives_400() -> None:
    buf = req("POST / HTTP/1.0", "Transfer-Encoding: chunked", body=b"0\r\n\r\n")
    assert_status(buf, 400)


# --- F2: unsupported transfer codings ----------------------------------------


@pytest.mark.parametrize("te", ["gzip, chunked", "deflate, chunked", "x, chunked"])
def test_f2_unsupported_coding_before_chunked_gives_501(te: str) -> None:
    assert_status(post(f"Transfer-Encoding: {te}", body=b"0\r\n\r\n"), 501)


def test_f2_unsupported_coding_on_separate_line_gives_501() -> None:
    buf = post(
        "Transfer-Encoding: gzip", "Transfer-Encoding: chunked", body=b"0\r\n\r\n"
    )
    assert_status(buf, 501)


# --- F3: chunked applied twice -----------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        ["Transfer-Encoding: chunked, chunked"],
        ["Transfer-Encoding: chunked", "Transfer-Encoding: chunked"],
        ["Transfer-Encoding: CHUNKED, chunked"],
    ],
)
def test_f3_chunked_twice_gives_400(fields: list[str]) -> None:
    assert_status(post(*fields, body=b"0\r\n\r\n"), 400)


# --- F4: framing and routing fields in trailers ------------------------------


@pytest.mark.parametrize(
    "trailer", ["Content-Length: 999", "transfer-encoding: chunked", "Host: evil"]
)
def test_f4_forbidden_trailer_gives_400(trailer: str) -> None:
    assert_status(chunked(b"0\r\n" + trailer.encode() + b"\r\n\r\n"), 400)


# --- F5: limits on the header section and the body ---------------------------


def test_f5_unterminated_header_line_gives_431() -> None:
    assert_status(b"GET / HTTP/1.1\r\nX: " + b"a" * 10_000_000, 431)


def test_f5_10mb_header_value_gives_431() -> None:
    assert_status(req("GET / HTTP/1.1", "Host: a", "X: " + "a" * 10_000_000), 431)


def test_f5_many_small_fields_over_the_byte_limit_give_431() -> None:
    fields = ["Host: a"] + [f"X-{i}: {'v' * 200}" for i in range(50)]
    assert_status(req("GET / HTTP/1.1", *fields), 431)


def test_f5_header_section_at_the_byte_limit_is_accepted() -> None:
    # "Host: a\r\n" + "X: " + value + "\r\n" + "\r\n" == MAX_HEADER_BYTES
    value = "v" * (MAX_HEADER_BYTES - 16)
    request, _ = parse_request(req("GET / HTTP/1.1", "Host: a", "X: " + value))
    assert request.get("X") == value


def test_f5_header_section_one_byte_over_the_limit_gives_431() -> None:
    value = "v" * (MAX_HEADER_BYTES - 15)
    assert_status(req("GET / HTTP/1.1", "Host: a", "X: " + value), 431)


def test_f5_unterminated_trailer_gives_431() -> None:
    assert_status(chunked(b"0\r\nX: " + b"a" * (MAX_HEADER_BYTES + 10)), 431)


@pytest.mark.parametrize("length", ["99999999999999999999", str(MAX_BODY + 1)])
def test_f5_content_length_over_the_limit_gives_413(length: str) -> None:
    assert_status(post(f"Content-Length: {length}"), 413)


def test_f5_content_length_at_the_limit_is_accepted() -> None:
    body = b"x" * MAX_BODY
    assert parse_request(post(f"Content-Length: {MAX_BODY}", body=body))[0].body == body


def test_f5_huge_chunk_size_gives_413_without_waiting() -> None:
    assert_status(chunked(b"ffffffffffffffff\r\n"), 413)


def test_f5_chunks_adding_up_over_the_limit_give_413() -> None:
    half = MAX_BODY // 2 + 1
    chunk = f"{half:x}\r\n".encode() + b"x" * half + b"\r\n"
    assert_status(chunked(chunk + chunk + b"0\r\n\r\n"), 413)


def test_f5_unterminated_chunk_size_line_gives_400() -> None:
    assert_status(chunked(b"5;" + b"e" * 10_000), 400)


# --- F6: one stray CRLF before the request-line ------------------------------
# Decision: ignore exactly one leading CRLF (RFC 9112 §2.2 SHOULD). Old clients
# send an extra CRLF after a POST body on keep-alive; rejecting it would turn
# the next valid request into a 400. A single CRLF can't be read two ways, so
# accepting it opens no smuggling gap. A bare LF or a second CRLF is still 400.


def test_f6_one_leading_crlf_is_ignored() -> None:
    request, _ = parse_request(b"\r\n" + req("GET / HTTP/1.1", "Host: a"))
    assert request.method == "GET"


@pytest.mark.parametrize("prefix", [b"\r\n\r\n", b"\n", b"\r\n\n"])
def test_f6_more_than_one_crlf_or_bare_lf_gives_400(prefix: bytes) -> None:
    assert_status(prefix + req("GET / HTTP/1.1", "Host: a"), 400)


def test_f6_extra_crlf_after_post_body_on_keep_alive() -> None:
    first = post("Content-Length: 3", body=b"abc")
    second = req("GET /next HTTP/1.1", "Host: a")
    _, rest = parse_request(first + b"\r\n" + second)
    assert parse_request(rest)[0].target == "/next"


# --- F7: Request is immutable all the way down -------------------------------


def test_f7_request_is_hashable() -> None:
    buf = chunked(b"1\r\na\r\n0\r\nT: 1\r\n\r\n")
    assert hash(parse_request(buf)[0]) == hash(parse_request(buf)[0])


def test_f7_headers_and_trailers_cannot_be_mutated() -> None:
    request, _ = parse_request(chunked(b"0\r\nT: 1\r\n\r\n"))
    assert isinstance(request.headers, tuple)
    assert isinstance(request.trailers, tuple)
    with pytest.raises(AttributeError):
        request.headers.append(("X", "1"))  # type: ignore[attr-defined]


# --- The 16 required cases ---------------------------------------------------
# The 14 rows of the h11/gunicorn comparison plus the two spec limits (414, 431).
# An int is the expected status; bytes is the expected body.

CASES = [
    ("host-space-colon", req("GET / HTTP/1.1", "Host : a"), 400),
    ("obs-fold", req("GET / HTTP/1.1", "Host: a", "X: 1", " 2"), 400),
    ("bare-lf", b"GET / HTTP/1.1\nHost: a\n\n", 400),
    ("no-host", req("GET / HTTP/1.1"), 400),
    ("cl-3-cl-4", post("Content-Length: 3", "Content-Length: 4", body=b"abcd"), 400),
    ("cl-3-cl-3", post("Content-Length: 3", "Content-Length: 3", body=b"abc"), b"abc"),
    ("cl-and-te", post("Content-Length: 3", "Transfer-Encoding: chunked"), 400),
    ("te-gzip", post("Transfer-Encoding: gzip", body=b"abc"), 400),
    ("no-length-abc", post(body=b"abc"), b""),
    ("cl-plus-3", post("Content-Length: +3", body=b"abc"), 400),
    ("te-Chunked", post("Transfer-Encoding: Chunked", body=b"0\r\n\r\n"), b""),
    ("cl-3-comma-3", post("Content-Length: 3, 3", body=b"abc"), b"abc"),
    ("te-chunked-gzip", post("Transfer-Encoding: chunked, gzip"), 400),
    ("cl-3-abcde", post("Content-Length: 3", body=b"abcde"), b"abc"),
    ("long-request-line", req("GET /" + "a" * 4100 + " HTTP/1.1", "Host: a"), 414),
    ("101-fields", req("GET / HTTP/1.1", *[f"X-{i}: v" for i in range(101)]), 431),
]


@pytest.mark.parametrize(
    ("buf", "expected"), [c[1:] for c in CASES], ids=[c[0] for c in CASES]
)
def test_required_case(buf: bytes, expected: int | bytes) -> None:
    if isinstance(expected, int):
        assert_status(buf, expected)
    else:
        assert parse_request(buf)[0].body == expected


def test_required_case_leftovers() -> None:
    assert parse_request(post(body=b"abc"))[1] == b"abc"
    assert parse_request(post("Content-Length: 3", body=b"abcde"))[1] == b"de"


# --- Incremental feeding -----------------------------------------------------

FEED_CASES = [
    req("GET /a HTTP/1.1", "Host: a", "X: 1"),
    req("GET /a HTTP/1.0"),
    b"\r\n" + req("GET /a HTTP/1.1", "Host: a"),
    post("Content-Length: 5", body=b"hello"),
    chunked(b"5;ext\r\nhello\r\n10\r\n" + b"x" * 16 + b"\r\n0\r\nT: 1\r\n\r\n"),
]


@pytest.mark.parametrize("buf", FEED_CASES)
def test_every_prefix_needs_more_data_and_full_buffer_parses(buf: bytes) -> None:
    for i in range(len(buf)):
        with pytest.raises(NeedMoreData):
            parse_request(buf[:i])
    _, rest = parse_request(buf)
    assert rest == b""


@pytest.mark.parametrize("first", FEED_CASES)
def test_pipelined_leftover_parses_as_second_request(first: bytes) -> None:
    second = req("POST /second HTTP/1.1", "Host: b", "Content-Length: 2", body=b"ok")
    _, rest = parse_request(first + second)
    assert rest == second
    request, rest = parse_request(rest)
    assert (request.target, request.body, rest) == ("/second", b"ok", b"")
