"""Bronze intake gate — file-shape checks and the virus-scan port.

OVERVIEW. Two independent things are under test: `check_file`
(src/bronze/filecheck.py), a pure function with no I/O, and `VirusScanPort`'s
adapters (src/bronze/scan.py) — `NullScanner` trivially, `ClamAVScanner`
against a minimal fake `clamd` server so the wire protocol is exercised
without a real ClamAV install in CI. Neither needs a database or an object
store, which is why this file lives apart from the end-to-end wiring test in
tests/handoff/test_intake_gate.py — that one needs the real tenant/pool/object
store fixtures this module deliberately avoids pulling in.
"""

from __future__ import annotations

import socket
import struct
import threading
from collections.abc import Iterator

import pytest

from src.bronze.filecheck import ALLOWED_EXTENSIONS, FileCheckResult, check_file
from src.bronze.scan import (
    ClamAVScanner,
    NullScanner,
    ScannerUnavailableError,
    ScanResult,
    build_scanner,
)
from src.core.config import Settings

pytestmark = pytest.mark.conformance


# =============================================================================
# check_file
# =============================================================================


def test_empty_file_is_rejected() -> None:
    assert check_file(filename="a.csv", data=b"") == FileCheckResult(
        ok=False, reason="file is empty"
    )


def test_oversized_file_is_rejected() -> None:
    result = check_file(filename="a.csv", data=b"x" * 100, max_bytes=50)
    assert not result.ok
    assert result.reason is not None
    assert "100 bytes" in result.reason
    assert "50-byte limit" in result.reason


def test_disallowed_extension_is_rejected() -> None:
    # "pdf" moved into ALLOWED_EXTENSIONS for the A2-A7 extraction adapters
    # (src/extraction/) — "exe" stands in as an extension that will never be
    # a legitimate Bronze intake format.
    result = check_file(filename="a.exe", data=b"hello")
    assert not result.ok
    assert result.reason is not None and "exe" in result.reason


def test_missing_filename_is_rejected_not_guessed_at() -> None:
    """No declared format is refused outright — see _extension_of's docstring."""
    result = check_file(filename=None, data=b"hello")
    assert not result.ok
    assert result.reason is not None and "''" in result.reason


@pytest.mark.parametrize("extension", sorted(ALLOWED_EXTENSIONS))
def test_every_declared_extension_is_accepted(extension: str) -> None:
    assert check_file(filename=f"export.{extension}", data=b"hello").ok


def test_extension_match_is_case_insensitive() -> None:
    assert check_file(filename="EXPORT.CSV", data=b"hello").ok


def test_checks_run_cheapest_first() -> None:
    """An empty AND wrongly-extensioned file reports emptiness, not the
    extension — check_file's docstring promises this ordering, so pin it."""
    result = check_file(filename="a.exe", data=b"")
    assert result.reason == "file is empty"


# =============================================================================
# NullScanner — scanning off, as an explicit adapter
# =============================================================================


def test_null_scanner_reports_everything_clean() -> None:
    assert NullScanner().scan(b"anything at all, even an EICAR string") == ScanResult(
        clean=True, scanner="none"
    )


# =============================================================================
# ClamAVScanner, against a minimal fake clamd
# =============================================================================


class _FakeClamd:
    """Just enough of clamd's INSTREAM wire protocol to test ClamAVScanner
    against: read the length-prefixed chunks, reply FOUND if a marker string
    was in the reassembled payload, OK otherwise. Not a virus scanner — a
    protocol double, so the adapter is tested against real bytes-on-a-socket
    rather than a mocked-out method that could drift from what clamd actually
    sends.
    """

    DIRTY_MARKER = b"EICAR-EXPECTED"

    def __init__(self) -> None:
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(1)
        self.host, self.port = self._listener.getsockname()
        threading.Thread(target=self._serve_one_connection, daemon=True).start()

    def _serve_one_connection(self) -> None:
        conn, _ = self._listener.accept()
        with conn:
            conn.recv(len(b"zINSTREAM\0"))  # the command line; unused past this
            payload = b""
            while True:
                header = conn.recv(4)
                if len(header) < 4:
                    break
                (chunk_length,) = struct.unpack("!L", header)
                if chunk_length == 0:
                    break
                payload += conn.recv(chunk_length)
            verdict = (
                b"stream: Test.Signature FOUND\0"
                if self.DIRTY_MARKER in payload
                else b"stream: OK\0"
            )
            conn.sendall(verdict)

    def close(self) -> None:
        self._listener.close()


@pytest.fixture
def fake_clamd() -> Iterator[_FakeClamd]:
    server = _FakeClamd()
    yield server
    server.close()


def test_clamav_scanner_reports_clean_bytes_as_clean(fake_clamd: _FakeClamd) -> None:
    scanner = ClamAVScanner(host=fake_clamd.host, port=fake_clamd.port)
    assert scanner.scan(b"perfectly ordinary CSV bytes") == ScanResult(
        clean=True, scanner="clamav"
    )


def test_clamav_scanner_reports_a_signature_when_flagged(fake_clamd: _FakeClamd) -> None:
    scanner = ClamAVScanner(host=fake_clamd.host, port=fake_clamd.port)
    result = scanner.scan(b"prefix " + fake_clamd.DIRTY_MARKER + b" suffix")
    assert result.clean is False
    assert result.scanner == "clamav"
    assert result.signature == "Test.Signature"


def test_clamav_scanner_raises_when_the_daemon_is_unreachable() -> None:
    """An unreachable scanner is an infrastructure fault, not a scan verdict —
    it must raise, never return ScanResult(clean=True, ...) by default."""
    scanner = ClamAVScanner(host="127.0.0.1", port=1, timeout_seconds=1.0)
    with pytest.raises(ScannerUnavailableError):
        scanner.scan(b"anything")


# =============================================================================
# build_scanner — the one place a provider is chosen
# =============================================================================


def test_build_scanner_defaults_to_null(settings: Settings) -> None:
    assert isinstance(build_scanner(settings), NullScanner)


def test_build_scanner_wires_clamav_from_settings(settings: Settings) -> None:
    configured = settings.model_copy(
        update={
            "virus_scan_provider": "clamav",
            "clamav_host": "scanner.internal",
            "clamav_port": 9999,
        }
    )
    scanner = build_scanner(configured)
    assert isinstance(scanner, ClamAVScanner)
    assert scanner._host == "scanner.internal"
    assert scanner._port == 9999


def test_build_scanner_rejects_an_unknown_provider(settings: Settings) -> None:
    configured = settings.model_copy(update={"virus_scan_provider": "not-a-real-provider"})
    with pytest.raises(ValueError, match="unknown virus_scan_provider"):
        build_scanner(configured)
