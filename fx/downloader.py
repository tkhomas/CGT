"""Download a file over HTTPS, verified, atomically.

Standard library only, plus curl as a fallback, no pip packages.

Order per URL:
  1. urllib, with Python's default verified context.
  2. If urllib fails on SSL verification only, curl, if present.
     Curl uses the system certificate store, the same store
     a browser uses, so it works on machines where Python's
     own CA bundle is broken or empty. Curl verification is
     not disabled either, it verifies with the system store.

On any other failure, curl is not tried for that URL,
the next URL in the list is tried instead. On failure,
nothing is saved, so a later run retries cleanly.
"""

from __future__ import annotations

import shutil
import ssl
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

_USER_AGENT = "Mozilla/5.0 (compatible; CGT-calculator)"


def _is_ssl_error(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.URLError):
        reason_text = str(exc.reason)
        return (
            isinstance(exc.reason, ssl.SSLCertVerificationError)
            or "CERTIFICATE_VERIFY_FAILED" in reason_text
        )
    return False


def _check_file(path: Path) -> str | None:
    """Return an error string if the downloaded file is bad, else None."""
    try:
        data = path.read_bytes()
    except Exception as exc:  # noqa: BLE001
        return f"could not read downloaded file: {exc}"
    if not data.strip():
        return "download returned an empty file"
    text = data.decode("utf-8", errors="ignore").strip()
    if text == "Resource not found" or text.lower().startswith("<!doctype html"):
        return f"download returned an error page, not a file: {text[:200]}"
    return None


def _download_urllib(url: str, tmp_path: Path) -> None:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "text/csv,application/xml,*/*",
        },
    )
    with urllib.request.urlopen(
        request, timeout=30, context=ssl.create_default_context()
    ) as response:
        data = response.read()
    tmp_path.write_bytes(data)


def _download_curl(url: str, tmp_path: Path) -> None:
    curl = shutil.which("curl")
    if curl is None:
        raise RuntimeError("curl not found on this machine")
    result = subprocess.run(
        [
            curl,
            "-f",
            "-L",
            "--max-time",
            "30",
            "-A",
            _USER_AGENT,
            "-o",
            str(tmp_path),
            url,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        detail = (result.stderr or "").strip()
        raise RuntimeError(f"curl failed with exit {result.returncode}: {detail}")


def download_file(urls: str | list[str], dest: Path) -> None:
    """Download the first working URL to dest."""
    if isinstance(urls, str):
        urls = [urls]
    errors: list[str] = []
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest.with_suffix(dest.suffix + ".tmp")
    for url in urls:
        # 1. urllib, the normal path.
        try:
            _download_urllib(url, tmp_path)
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="ignore").strip()
            except Exception:  # noqa: BLE001
                body = ""
            detail = f"HTTP Error {exc.code}: {exc.reason}"
            if body:
                detail += f", body: {body[:200]}"
            errors.append(f"{url}: {detail}")
            continue
        except Exception as exc:  # noqa: BLE001
            if not _is_ssl_error(exc):
                errors.append(f"{url}: urllib failed: {exc}")
                continue
            # 2. SSL only: fall back to curl for this URL.
            errors.append(f"{url}: urllib SSL failed: {exc}")
            try:
                _download_curl(url, tmp_path)
            except Exception as curl_exc:  # noqa: BLE001
                errors.append(f"{url}: curl failed: {curl_exc}")
                continue
        # Either path wrote tmp_path, validate it before publishing.
        bad = _check_file(tmp_path)
        if bad is not None:
            errors.append(f"{url}: {bad}")
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
            continue
        tmp_path.replace(dest)
        return
    try:
        tmp_path.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
    raise RuntimeError(
        "Download failed. "
        + "; ".join(errors)
        + f" As a workaround, download the file manually and place it at {dest}."
    )
