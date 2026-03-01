# -*- coding: utf-8 -*-
"""Nextcloud Files API client for accessing shared files."""

import os
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from urllib.parse import quote, urljoin

import aiohttp

logger = logging.getLogger(__name__)


class NextcloudFilesClient:
    """
    Client for Nextcloud Files API (WebDAV).
    
    Used to access file metadata and download links for files shared in Talk.
    """
    
    def __init__(self, base_url: str, username: str = "", password: str = ""):
        """
        Initialize Nextcloud Files client.

        Args:
            base_url: Base URL of Nextcloud server (e.g., https://example.com/nextcloud)
            username: Username for authentication (optional for public shares)
            password: Password or app token for authentication
        """
        self.base_url = base_url.rstrip('/')
        self.username = username
        self.password = password
        self._session: Optional[aiohttp.ClientSession] = None

    def build_webdav_url(self, api_user: str, file_path: str) -> Optional[str]:
        """
        Build a WebDAV download URL for a Nextcloud file.

        Uses the format: {base_url}/remote.php/dav/files/{api_user}/{file_path}
        Following OpenClaw PR #29256 implementation.

        Args:
            api_user: API username for the bot account
            file_path: File path relative to user's files root (e.g., Talk/image.jpg)

        Returns:
            WebDAV URL or None if parameters are insufficient

        Example:
            >>> client.build_webdav_url("bot-user", "Talk/image.jpg")
            'https://cloud.example.com/nextcloud/remote.php/dav/files/bot-user/Talk/image.jpg'
        """
        if not self.base_url or not api_user or not file_path:
            return None

        # Normalize base URL
        base_url = self.base_url.rstrip("/")

        # Encode file path: split by "/" and encode each segment
        # This ensures spaces and special characters are properly encoded
        encoded_path_segments = [quote(segment) for segment in file_path.split("/")]
        encoded_path = "/".join(encoded_path_segments)

        # Encode the username
        encoded_user = quote(api_user)

        # Build WebDAV URL
        webdav_url = f"{base_url}/remote.php/dav/files/{encoded_user}/{encoded_path}"

        return webdav_url
    
    async def get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session."""
        if self._session is None:
            auth = aiohttp.BasicAuth(self.username, self.password) if self.username else None
            self._session = aiohttp.ClientSession(auth=auth)
        return self._session
    
    async def close(self):
        """Close the session."""
        if self._session:
            await self._session.close()
            self._session = None
    
    async def get_file_info(self, file_path: str) -> Optional[Dict[str, Any]]:
        """
        Get file information via WebDAV PROPFIND request.
        
        Args:
            file_path: Path to file relative to user's files root
            
        Returns:
            File information dict or None
        """
        try:
            session = await self.get_session()
            
            # Build WebDAV URL
            dav_url = f"{self.base_url}/remote.php/dav/files/{quote(file_path.lstrip('/'))}"
            
            # PROPFIND request to get file properties
            propfind_body = '''<?xml version="1.0"?>
            <d:propfind xmlns:d="DAV:" xmlns:nc="http://nextcloud.org/ns" xmlns:oc="http://owncloud.org/ns">
                <d:prop>
                    <d:getlastmodified/>
                    <d:getcontentlength/>
                    <d:getcontenttype/>
                    <d:resourcetype/>
                    <oc:id/>
                    <oc:owner-id/>
                    <oc:owner-display-name/>
                    <nc:has-preview/>
                </d:prop>
            </d:propfind>'''
            
            headers = {
                'Depth': '0',
                'Content-Type': 'application/xml',
            }
            
            async with session.propfind(dav_url, data=propfind_body, headers=headers) as resp:
                if resp.status == 207:  # Multi-Status
                    xml_content = await resp.text()
                    # Parse XML response (simplified - full implementation would use xml.etree.ElementTree)
                    logger.debug(f"Got file info for {file_path}")
                    return {
                        'path': file_path,
                        'success': True,
                    }
                else:
                    logger.warning(f"Failed to get file info: {resp.status}")
                    return None
                    
        except Exception as e:
            logger.exception(f"Error getting file info: {e}")
            return None
    
    async def get_file_download_url(self, file_path: str) -> Optional[str]:
        """
        Get direct download URL for a file.
        
        Args:
            file_path: Path to file relative to user's files root
            
        Returns:
            Download URL or None
        """
        try:
            # For authenticated access, use WebDAV endpoint
            download_url = f"{self.base_url}/remote.php/dav/files/{quote(file_path.lstrip('/'))}"
            logger.debug(f"Generated download URL for {file_path}")
            return download_url
            
        except Exception as e:
            logger.exception(f"Error generating download URL: {e}")
            return None
    
    async def get_file_preview_url(self, file_path: str, max_width: int = 384, max_height: int = 384) -> Optional[str]:
        """
        Get preview image URL for a file (if available).
        
        Args:
            file_path: Path to file
            max_width: Maximum preview width
            max_height: Maximum preview height
            
        Returns:
            Preview URL or None
        """
        try:
            # Use Nextcloud preview endpoint
            preview_url = (
                f"{self.base_url}/index.php/core/preview.png?"
                f"file={quote(file_path)}&"
                f"x={max_width}&y={max_height}&a=1"
            )
            logger.debug(f"Generated preview URL for {file_path}")
            return preview_url
            
        except Exception as e:
            logger.exception(f"Error generating preview URL: {e}")
            return None
    
    async def get_public_share_link(self, share_token: str, path: str = "") -> Optional[str]:
        """
        Get public share link for a file.
        
        Args:
            share_token: Share token (from file_shared event parameters)
            path: Optional path within the share
            
        Returns:
            Public share URL or None
        """
        try:
            if path:
                share_url = f"{self.base_url}/index.php/s/{share_token}/files?path={quote(path)}"
            else:
                share_url = f"{self.base_url}/index.php/s/{share_token}"
            
            logger.debug(f"Generated share link: {share_url}")
            return share_url
            
        except Exception as e:
            logger.exception(f"Error generating share link: {e}")
            return None
    
    async def download_file(self, url: str, local_path: str) -> bool:
        """
        Download file from URL to local path, handling Nextcloud authentication.
        
        For Nextcloud share URLs (/s/...) and WebDAV URLs, this method uses
        Basic Auth if credentials are available.

        Args:
            url: URL to download from
            local_path: Local file path to save to

        Returns:
            True if successful, False otherwise
        """
        logger.info(f"NextcloudFilesClient.download_file: url={url[:50]}..., local_path={local_path}")

        try:
            session = await self.get_session()

            # Extract filename from URL if needed
            from urllib.parse import urlparse, unquote
            parsed = urlparse(url)
            url_filename = unquote(os.path.basename(parsed.path))

            logger.info(f"Downloading file from Nextcloud: url={url} -> {local_path}")

            # Check if this is a Nextcloud URL that needs auth
            is_share_url = '/s/' in url
            is_webdav_url = '/remote.php/dav/files/' in url
            needs_auth = (is_share_url or is_webdav_url) and self.username and self.password

            logger.info(f"Nextcloud download auth: is_share={is_share_url}, is_webdav={is_webdav_url}, needs_auth={needs_auth}, username={self.username[:2]}...")

            # Prepare headers
            headers = {}
            # For share URLs and WebDAV URLs, use Authorization header if credentials are available
            if needs_auth:
                import base64
                credentials = f"{self.username}:{self.password}"
                encoded_credentials = base64.b64encode(credentials.encode('utf-8')).decode('ascii')
                headers['Authorization'] = f"Basic {encoded_credentials}"
                logger.info(f"Added Authorization header for WebDAV download")

            # Download with aiohttp (preserves any auth headers)
            import aiohttp
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as http_session:
                async with http_session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        resp_text = await resp.text()
                        logger.error(f"Failed to download file: status={resp.status}, resp={resp_text[:200]}")
                        return False

                    # Verify content is not HTML (login page)
                    content = await resp.read()
                    if content.startswith(b'<!DOCTYPE') or content.startswith(b'<html'):
                        logger.error(f"Downloaded content is HTML (login page), not binary file: url={url}")
                        return False

                    # Save to file
                    local_path_obj = Path(local_path)
                    local_path_obj.parent.mkdir(parents=True, exist_ok=True)
                    with open(local_path_obj, 'wb') as f:
                        f.write(content)

                    logger.info(f"Successfully downloaded file to {local_path} ({len(content)} bytes)")
                    return True

        except Exception as e:
            logger.exception(f"Error downloading file from Nextcloud: {e}")
            return False

    async def download_file_via_webdav(
        self,
        api_user: str,
        file_path: str,
        local_path: str,
    ) -> bool:
        """
        Download file using WebDAV URL with authentication.

        This follows the OpenClaw PR #29256 implementation, using:
        {base_url}/remote.php/dav/files/{api_user}/{file_path}

        Args:
            api_user: API username for the bot account
            file_path: File path relative to user's files root
            local_path: Local file path to save to

        Returns:
            True if successful, False otherwise
        """
        try:
            # Build WebDAV URL
            webdav_url = self.build_webdav_url(api_user, file_path)
            if not webdav_url:
                logger.error("Failed to build WebDAV URL")
                return False

            logger.info(f"Downloading via WebDAV: {webdav_url} -> {local_path}")

            # Get session with authenticated Basic Auth
            session = await self.get_session()

            # Download file
            timeout = aiohttp.ClientTimeout(total=60)
            async with session.get(webdav_url, timeout=timeout) as resp:
                if resp.status != 200:
                    resp_text = await resp.text()
                    logger.error(
                        f"WebDAV download failed: status={resp.status}, url={webdav_url}, resp={resp_text[:200]}"
                    )
                    return False

                # Read content
                content = await resp.read()

                # Verify content is not HTML (shouldn't happen with WebDAV)
                if content.startswith(b'<!DOCTYPE') or content.startswith(b'<html'):
                    logger.error(f"Downloaded content is HTML (unexpected): url={webdav_url}")
                    return False

                # Save to file
                local_path_obj = Path(local_path)
                local_path_obj.parent.mkdir(parents=True, exist_ok=True)
                with open(local_path_obj, 'wb') as f:
                    f.write(content)

                logger.info(
                    f"Successfully downloaded via WebDAV: {local_path} ({len(content)} bytes)"
                )
                return True

        except Exception as e:
            logger.exception(f"Error downloading file via WebDAV: {e}")
            return False


async def create_nextcloud_files_client(
    backend_url: str,
    username: str = "",
    password: str = "",
) -> NextcloudFilesClient:
    """
    Create a Nextcloud Files client instance.
    
    Args:
        backend_url: Nextcloud backend URL
        username: Optional username
        password: Optional password/token
        
    Returns:
        NextcloudFilesClient instance
    """
    return NextcloudFilesClient(backend_url, username, password)
