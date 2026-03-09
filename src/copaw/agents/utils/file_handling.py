# -*- coding: utf-8 -*-
"""File handling utilities for downloading and managing files.

This module provides utilities for:
- Downloading files from base64 encoded data
- Downloading files from URLs
- Managing download directories
"""
import os
import mimetypes
import base64
import hashlib
import logging
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _resolve_local_path(
    url: str,
    parsed: urllib.parse.ParseResult,
) -> Optional[str]:
    """Return local file path for file:// or plain path; None for remote."""
    if parsed.scheme == "file":
        local_path = Path(urllib.request.url2pathname(parsed.path))
        if not local_path.exists():
            raise FileNotFoundError(f"Local file not found: {local_path}")
        if local_path.is_file() and local_path.stat().st_size == 0:
            raise ValueError(f"Local file is empty: {local_path}")
        return str(local_path.resolve())
    if parsed.scheme == "" and parsed.netloc == "":
        p = Path(url).expanduser()
        if p.exists():
            if p.is_file() and p.stat().st_size == 0:
                raise ValueError(f"Local file is empty: {p}")
            return str(p.resolve())
    # Windows absolute path: urlparse("C:\\path") -> scheme="c", path="\\path"
    if (
        os.name == "nt"
        and len(parsed.scheme) == 1
        and parsed.scheme.isalpha()
        and (parsed.path.startswith("\\") or parsed.path.startswith("/"))
    ):
        p = Path(url.strip()).resolve()
        if p.exists() and p.is_file():
            if p.stat().st_size == 0:
                raise ValueError(f"Local file is empty: {p}")
            return str(p)
    return None


def _download_remote_to_path(url: str, local_file_path: Path) -> None:
    """
    Download url to local_file_path via wget, curl, or urllib. Raises on fail.
    """
    try:
        subprocess.run(
            ["wget", "-q", "-O", str(local_file_path), url],
            capture_output=True,
            timeout=60,
            check=True,
        )
        logger.debug("Downloaded file via wget to: %s", local_file_path)
        return
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.debug("wget failed, trying curl: %s", e)
    try:
        subprocess.run(
            ["curl", "-s", "-L", "-o", str(local_file_path), url],
            capture_output=True,
            timeout=60,
            check=True,
        )
        logger.debug("Downloaded file via curl to: %s", local_file_path)
        return
    except (subprocess.CalledProcessError, FileNotFoundError) as curl_err:
        logger.debug("curl failed, trying urllib: %s", curl_err)
    try:
        urllib.request.urlretrieve(url, str(local_file_path))
        logger.debug("Downloaded file via urllib to: %s", local_file_path)
    except Exception as urllib_err:
        logger.error(
            "wget, curl and urllib all failed for URL %s: %s",
            url,
            urllib_err,
        )
        raise RuntimeError(
            "Failed to download file: wget, curl and urllib all failed",
        ) from urllib_err


def _guess_suffix_from_url_headers(url: str) -> Optional[str]:
    """
    HEAD request to get Content-Type and return a suffix like '.pdf'.
    Used to fix DingTalk download URLs that always return .file extension.
    Returns None on any failure (e.g. OSS forbids HEAD or returns no type).
    """
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = (
                (resp.headers.get("Content-Type") or "").split(";")[0].strip()
            )
            if not raw:
                return None
            suffix = mimetypes.guess_extension(raw)
            return suffix if suffix else None
    except Exception:
        return None


# Magic bytes (prefix) -> suffix for .file fallback when HEAD fails (e.g. OSS).
_MAGIC_SUFFIX: list[tuple[bytes, str]] = [
    (b"%PDF", ".pdf"),
    (b"PK\x03\x04", ".zip"),
    (b"PK\x05\x06", ".zip"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"\xd0\xcf\x11\xe0", ".doc"),  # MS Office (doc, xls, ppt)
    (b"RIFF", ".webp"),  # or .wav; webp has RIFF....WEBP
]


def _guess_suffix_from_file_content(path: Path) -> Optional[str]:
    """
    Guess file extension from magic bytes. Used when URL HEAD fails (e.g. OSS).
    Returns suffix like '.pdf' or None.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(32)
        for magic, suffix in _MAGIC_SUFFIX:
            if head.startswith(magic):
                return suffix
        return None
    except Exception:
        return None


async def download_file_from_base64(
    base64_data: str,
    filename: Optional[str] = None,
    download_dir: str = "downloads",
) -> str:
    """
    Save base64-encoded file data to local download directory.

    Args:
        base64_data: Base64-encoded file content.
        filename: The filename to save. If not provided, will generate one.
        download_dir: The directory to save files. Defaults to "downloads".

    Returns:
        The local file path.
    """
    try:
        file_content = base64.b64decode(base64_data)

        download_path = Path(download_dir)
        download_path.mkdir(parents=True, exist_ok=True)

        if not filename:
            file_hash = hashlib.md5(file_content).hexdigest()
            filename = f"file_{file_hash}"

        local_file_path = download_path / filename
        with open(local_file_path, "wb") as f:
            f.write(file_content)

        logger.debug("Downloaded file to: %s", local_file_path)
        return str(local_file_path.absolute())

    except Exception as e:
        logger.error("Failed to download file from base64: %s", e)
        raise


async def download_file_from_url(
    url: str,
    filename: Optional[str] = None,
    download_dir: str = "downloads",
) -> str:
    """
    Download a file from URL to local download directory using wget or curl.

    Args:
        url (`str`):
            The URL of the file to download.
        filename (`str`, optional):
            The filename to save. If not provided, will extract from URL or
            generate a hash-based name.
        download_dir (`str`):
            The directory to save files. Defaults to "downloads".

    Returns:
        `str`:
            The local file path.
    """
    logger.info(f"[file_handling] download_file_from_url: url={url[:60]}..., filename={filename}")
    try:
        # Check if this is a Nextcloud URL that needs special handling
        # Nextcloud share URLs like /s/... need authentication
        is_nextcloud_share = '/nextcloud/s/' in url or '/s/' in url
        
        # For Nextcloud URLs, try to use NextcloudFilesClient if credentials are available
        # Support both share URLs (/s/...) and WebDAV URLs (/remote.php/dav/files/...)
        is_nextcloud_url = '/nextcloud/' in url or '/remote.php/dav/files/' in url

        if is_nextcloud_url:
            try:
                # Extract base URL from the URL
                # Handle both share URLs and WebDAV URLs
                parsed = urllib.parse.urlparse(url)
                split_path = parsed.path.split('/')
                base_parts = []

                # For WebDAV URL: /remote.php/dav/files/{user}/{path}
                # For share URL: /s/{token}
                if '/remote.php/dav/files/' in url:
                    # WebDAV URL - extract base up to /remote
                    for part in split_path:
                        if part == 'remote.php':
                            break
                        base_parts.append(part)
                    base_path = '/'.join(base_parts)
                    base_url = f"{parsed.scheme}://{parsed.netloc}{base_path}"

                    is_webdav = True
                else:
                    # Share URL - extract base up to /s
                    for part in split_path:
                        if part == 's':
                            break
                        base_parts.append(part)
                    base_path = '/'.join(base_parts)
                    base_url = f"{parsed.scheme}://{parsed.netloc}{base_path}"

                    is_webdav = False

                # Try to get Nextcloud credentials from environment
                nc_username = os.getenv("NEXTCLOUD_USERNAME", "")
                nc_password = os.getenv("NEXTCLOUD_PASSWORD", "")

                if nc_username and nc_password:
                    logger.info(f"Using NextcloudFilesClient for download: {url} (is_webdav={is_webdav})")
                    from app.channels.nextcloud_talk.files_client import NextcloudFilesClient

                    # Prepare local file path
                    download_path = Path(download_dir)
                    download_path.mkdir(parents=True, exist_ok=True)

                    if not filename:
                        url_filename = os.path.basename(parsed.path)
                        filename = url_filename if url_filename else f"file_{hashlib.md5(url.encode()).hexdigest()}"

                    local_file_path = download_path / filename

                    # Download using NextcloudFilesClient
                    client = NextcloudFilesClient(base_url, nc_username, nc_password)

                    logger.info(f"[file_handling] Creating NextcloudFilesClient: base_url={base_url[:40]}..., username={nc_username[:10]}...")
                    logger.info(f"[file_handling] About to call client.download_file: url={url[:60]}..., local_path={str(local_file_path)[:60]}")

                    if is_webdav:
                        # For WebDAV URL, download directly (credentials already in session)
                        success = await client.download_file(url, str(local_file_path))
                    else:
                        # For share URL, also try direct download with auth
                        success = await client.download_file(url, str(local_file_path))

                    await client.close()

                    if success and local_file_path.exists():
                        # Verify file is not HTML
                        with open(local_file_path, 'rb') as f:
                            header = f.read(16)
                            if header.startswith(b'<!DOCTYPE') or header.startswith(b'<html'):
                                raise ValueError("Downloaded file is HTML, not expected binary data")

                        logger.info(f"Successfully downloaded via NextcloudFilesClient: {local_file_path}")
                        return str(local_file_path.absolute())
                    else:
                        logger.warning(f"NextcloudFilesClient download failed (success={success}), returning error")
                        # Don't fallback to wget/curl for WebDAV URLs - they need authentication
                        raise RuntimeError(f"NextcloudFilesClient download failed for URL: {url}")
                else:
                    logger.info("No Nextcloud credentials found, using wget/curl for download")
            except Exception as nc_error:
                logger.debug(f"Nextcloud auth download failed: {nc_error}, falling back to wget/curl")
                # Continue to fallback method
        
        # Standard download path (non-Nextcloud or fallback)
        parsed = urllib.parse.urlparse(url)
        local = _resolve_local_path(url, parsed)
        if local is not None:
            return local

        download_path = Path(download_dir)
        download_path.mkdir(parents=True, exist_ok=True)
        if not filename:
            url_filename = os.path.basename(parsed.path)
            filename = (
                url_filename
                if url_filename
                else f"file_{hashlib.md5(url.encode()).hexdigest()}"
            )
        local_file_path = download_path / filename
        _download_remote_to_path(url, local_file_path)
        if not local_file_path.exists():
            raise FileNotFoundError("Downloaded file does not exist")
        if local_file_path.stat().st_size == 0:
            raise ValueError("Downloaded file is empty")
        
        # Verify file is not HTML (check for login page)
        with open(local_file_path, 'rb') as f:
            header = f.read(16)
            if header.startswith(b'<!DOCTYPE') or header.startswith(b'<html'):
                logger.warning(f"Downloaded file appears to be HTML (login page) for URL: {url}")
                raise ValueError(f"Downloaded file is HTML (login page), not expected binary data. URL may require authentication.")
        
        # DingTalk (and similar) return URLs that save as .file; replace with
        # real extension. Try HEAD first; if that fails (e.g. OSS), use magic.
        if local_file_path.suffix == ".file":
            real_suffix = _guess_suffix_from_url_headers(url)
            if not real_suffix:
                real_suffix = _guess_suffix_from_file_content(local_file_path)
            if real_suffix:
                new_path = local_file_path.with_suffix(real_suffix)
                local_file_path.rename(new_path)
                local_file_path = new_path
                logger.debug(
                    "Replaced .file with %s for %s",
                    real_suffix,
                    local_file_path,
                )
        return str(local_file_path.absolute())
    except subprocess.TimeoutExpired as e:
        logger.error("Download timeout for URL: %s", url)
        raise TimeoutError(f"Download timeout for URL: {url}") from e
    except Exception as e:
        logger.error("Failed to download file from URL %s: %s", url, e)
        raise
