"""Virus-scan port for Bronze intake, and the adapters behind it.

OVERVIEW. `VirusScanPort` is the contract `BronzeIngestionService` depends on:
one method, `scan(data) -> ScanResult`. Two adapters implement it today —
`NullScanner` (scanning off, the explicit default) and `ClamAVScanner` (a real,
free, self-hostable scanner, wired via `build_scanner`). `build_scanner` is the
one place a provider is chosen, from `Settings.virus_scan_provider`.

WHY A PORT, NOT A LIBRARY CALL. Steve has said the scanner is a boundary that
will move: dev runs with nothing, a later environment might call a commercial
API or a cloud-native scanner (AWS GuardDuty Malware Protection and similar),
and any environment must be able to turn scanning off entirely for a pipeline
run. All three of those are "which adapter does `build_scanner` construct",
never a change to `BronzeIngestionService.receive` — that is what buys the
ability to swap providers without touching the ingestion path, which is the
whole point of writing this as a `Protocol` instead of importing `clamd` (or a
vendor SDK) directly into `service.py`.

WHERE THIS RUNS. `BronzeIngestionService.receive` calls `scan()` after the
dedup check and before the object is written to storage — a duplicate upload
reuses the prior scan verdict instead of re-scanning identical bytes, and a
positive result must arrive before anything is written under Object Lock,
because Object Lock makes the object unmodifiable once it lands.
"""

from __future__ import annotations

import logging
import socket
import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from src.core.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ScanResult:
    """The verdict one scanner adapter returned for one artefact's bytes.

    Attributes:
        clean: True if the scanner inspected the bytes and found nothing.
            False means `signature` is populated with what it found, and the
            caller must refuse the file — never store bytes a scanner has
            flagged.
        scanner: Which adapter produced this verdict ("none", "clamav", ...).
            Recorded on a rejection so the reason a file was refused survives
            a later provider swap — "clamav flagged this as X" stays
            meaningful even after the tenant moves to a different scanner.
        signature: The threat name the scanner reported, when `clean` is
            False. `None` when clean, or when the scanner found something but
            did not name it.
    """

    clean: bool
    scanner: str
    signature: str | None = None


class VirusScanPort(Protocol):
    """What Bronze intake needs from a virus scanner, and nothing more.

    Any adapter — `NullScanner`, `ClamAVScanner`, or a future commercial/cloud
    adapter — satisfies this by implementing `scan`. `BronzeIngestionService`
    depends on this Protocol, never on a concrete class, which is what makes
    "swap the scanner" a one-function change (`build_scanner`) rather than a
    change to the ingestion path.
    """

    def scan(self, data: bytes) -> ScanResult:
        """Scan `data` and return a verdict.

        Finding a virus is a normal, expected outcome carried in the return
        value (`ScanResult(clean=False, ...)`) — implementations must not
        raise for that. Raising is reserved for the scanner itself being
        unreachable or misbehaving (see `ScannerUnavailableError`), which the
        caller needs to distinguish from "scanned clean" and "scanned dirty"
        because it calls for a different response (fail the upload loudly
        rather than silently treating an unreachable scanner as a pass).
        """
        ...


class ScannerUnavailableError(Exception):
    """The scanner adapter could not complete a scan at all.

    Infrastructure failure (the daemon is down, the API call timed out) —
    never raised for "the file contains malware", which is `ScanResult(clean=
    False, ...)`. Kept as a plain `Exception` rather than a `TenantDataError`
    subclass: this is an operational fault, not a tenant-isolation or
    validation event, so it should not be caught by code that is only
    watching for those.
    """


class NullScanner:
    """The "scanning is off" adapter — an explicit choice, not an accident.

    Selected by `build_scanner` when `Settings.virus_scan_provider == "none"`,
    which is also the default. Every artefact is reported clean without
    inspection. This exists as a named class, rather than `scanner is None`
    checks scattered through `BronzeIngestionService`, so "no scanning" is
    itself one branch of the same Protocol every real adapter implements —
    turning scanning on later is choosing a different adapter, not adding a
    new code path.
    """

    def scan(self, data: bytes) -> ScanResult:
        """Always clean. `data` is intentionally unread — see class docstring."""
        del data
        return ScanResult(clean=True, scanner="none")


class ClamAVScanner:
    """Adapter over a running `clamd` daemon, via its `INSTREAM` command.

    ClamAV is free and self-hostable, which is why it is the first REAL
    adapter here rather than a stub: it can be exercised in CI and locally
    without a paid API key or cloud credentials. It is not presumed to be the
    permanent choice — per Steve's plan to evaluate a commercial or
    cloud-native scanner, replacing it later means writing one more class that
    satisfies `VirusScanPort` and pointing `build_scanner` at it. Nothing in
    `src/bronze/service.py` names `ClamAVScanner` or `clamd` at all.
    """

    #: clamd's INSTREAM protocol is a stream of length-prefixed chunks
    #: terminated by a zero-length chunk. 8 KiB keeps memory bounded on a large
    #: register export without meaningfully slowing the scan.
    _CHUNK_BYTES = 8192

    def __init__(self, *, host: str, port: int, timeout_seconds: float = 10.0) -> None:
        self._host = host
        self._port = port
        self._timeout_seconds = timeout_seconds

    def scan(self, data: bytes) -> ScanResult:
        """Stream `data` to clamd over a fresh TCP connection and parse its reply.

        Raises `ScannerUnavailableError` if clamd cannot be reached or replies with
        something this adapter does not recognise — both are infrastructure
        problems the caller must treat differently from a clean or dirty scan.
        """
        try:
            with socket.create_connection(
                (self._host, self._port), timeout=self._timeout_seconds
            ) as sock:
                self._stream(sock, data)
                raw_reply = sock.recv(4096)
        except OSError as exc:
            raise ScannerUnavailableError(
                f"clamd at {self._host}:{self._port} unreachable: {exc}"
            ) from exc

        return self._parse_reply(raw_reply)

    def _stream(self, sock: socket.socket, data: bytes) -> None:
        """Send `data` as clamd's INSTREAM wire format: `zINSTREAM\\0`, then
        each chunk as a 4-byte big-endian length followed by the chunk bytes,
        then a zero-length chunk to signal end of stream."""
        sock.sendall(b"zINSTREAM\0")
        for offset in range(0, len(data), self._CHUNK_BYTES):
            chunk = data[offset : offset + self._CHUNK_BYTES]
            sock.sendall(struct.pack("!L", len(chunk)) + chunk)
        sock.sendall(struct.pack("!L", 0))

    def _parse_reply(self, raw_reply: bytes) -> ScanResult:
        """clamd replies `stream: OK` when clean, or `stream: <Signature>
        FOUND` when not. Anything else means this adapter and the daemon have
        disagreed about the protocol, which is an infrastructure fault, not a
        scan verdict — silently treating it as clean would be exactly the
        failure mode a virus scanner exists to prevent."""
        reply = raw_reply.decode("utf-8", errors="replace").strip("\0").strip()
        if reply.endswith("OK"):
            return ScanResult(clean=True, scanner="clamav")
        if "FOUND" in reply:
            signature = reply.removeprefix("stream:").removesuffix("FOUND").strip()
            return ScanResult(clean=False, scanner="clamav", signature=signature or None)
        raise ScannerUnavailableError(f"unrecognised clamd reply: {reply!r}")


def build_scanner(settings: Settings) -> VirusScanPort:
    """Construct the scanner named by `Settings.virus_scan_provider`.

    The one place a provider decision is made. Everything upstream —
    `BronzeIngestionService`, callers that construct it — depends on
    `VirusScanPort` and never imports a concrete adapter, so adding a third
    provider is one new `case` here, not a change anywhere else.
    """
    match settings.virus_scan_provider:
        case "none":
            return NullScanner()
        case "clamav":
            return ClamAVScanner(host=settings.clamav_host, port=settings.clamav_port)
        case other:
            raise ValueError(
                f"unknown virus_scan_provider {other!r}, expected 'none' or 'clamav'"
            )
