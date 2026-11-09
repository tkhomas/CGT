"""Download a file over HTTPS with curl, verified, atomically.

Curl uses the system certificate store, the same store a
browser uses, so it works on machines where Python's own
CA bundle does not trust the site. Verification is not
disabled.

Curl is a prerequisite, not an optional fallback. If curl
is not found, the download fails with a clear error.

On success, a message is printed to stderr naming the URL
and the destination, so a download is never silent.
On failure, nothing is saved, so a later run retries
cleanly, and a forced redownload never clobbers a cached file
with a failed download.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_USER_AGENT = "Mozilla/5.0 (compatible; CGT-calculator)"


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


def _download_curl(url: str, tmp_path: Path) -> None:
    curl = shutil.which("curl")
    if curl is None:
        raise RuntimeError(
            "curl not found on this machine: curl is required to download FX rates"
        )
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
        try:
            _download_curl(url, tmp_path)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{url}: {exc}")
            continue
        # Curl wrote tmp_path, validate it before publishing.
        bad = _check_file(tmp_path)
        if bad is not None:
            errors.append(f"{url}: {bad}")
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception as exc:  # noqa: BLE001
                # Cleanup only, never masks the download error.
                print(
                    f"Warning: could not remove temporary download file "
                    f"{tmp_path}: {exc}",
                    file=sys.stderr,
                )
            continue
        tmp_path.replace(dest)
        print(f"Downloaded {url} to {dest}", file=sys.stderr)
        return
    try:
        tmp_path.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        # Cleanup only, never masks the download error.
        print(
            f"Warning: could not remove temporary download file {tmp_path}: {exc}",
            file=sys.stderr,
        )
    raise RuntimeError(
        "Download failed. "
        + "; ".join(errors)
        + f". As a workaround, download the file manually and place it at {dest}."
    )
