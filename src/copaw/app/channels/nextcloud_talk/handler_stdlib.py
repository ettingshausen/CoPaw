# -*- coding: utf-8 -*-
"""Python standard library HTTP handler for Nextcloud Talk webhook."""

import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Callable, Dict

from .content_utils import (
    NextcloudTalkContentParser,
    session_param_from_token,
)
from .utils import (
    verify_request_signature,
    extract_backend_url,
)
from .constants import (
    HEADER_SIGNATURE,
    HEADER_RANDOM,
    HEADER_BACKEND,
)

logger = logging.getLogger(__name__)


class NextcloudTalkWebhookHandler(BaseHTTPRequestHandler):
    """
    HTTP webhook handler for Nextcloud Talk using Python standard library.

    Receives POST requests from Nextcloud Talk,
    verifies signatures, and forwards to the channel's enqueue callback.
    """

    # Class-level attributes (set by channel)
    _enqueue_callback: Any = None
    _webhook_secret: str = ""
    _bot_prefix: str = ""
    _api_user: str = ""  # Same as username (BOT account for WebDAV access)

    def __init__(self, *args, **kwargs):
        """
        Initialize handler.

        Class-level attributes (set by channel):
        - _enqueue_callback: Function to enqueue payloads to channel
        - _webhook_secret: Shared secret for signature verification
        - _bot_prefix: Bot message prefix
        - _api_user: Bot account username for WebDAV access (alias for username)
        """
        self._enqueue_callback = self.__class__._enqueue_callback
        self._webhook_secret = self.__class__._webhook_secret
        self._bot_prefix = self.__class__._bot_prefix
        self._api_user = self.__class__._api_user
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        """Suppress default http.server logging"""
        logger.info(f"nextcloud_talk webhook: {format % args}")

    def do_POST(self):
        """Handle POST requests from Nextcloud Talk."""
        # Only handle the webhook path
        if not self.path.startswith("/webhook/nextcloud_talk"):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"error":"Not found"}')
            return

        try:
            # Extract headers
            signature = self.headers.get(HEADER_SIGNATURE, "")
            random = self.headers.get(HEADER_RANDOM, "")
            backend = self.headers.get(HEADER_BACKEND, "")

            logger.info(
                f"nextcloud_talk webhook: backend_suffix={backend[-20:] if backend else 'None'} "
                f"signature_len={len(signature)} random_len={len(random)}"
            )

            # Read request body
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)

            # Verify signature
            if not verify_request_signature(body, signature, random, self._webhook_secret):
                logger.warning("nextcloud_talk webhook: signature verification failed")
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b'{"error":"Invalid signature"}')
                return

            # Parse JSON payload
            try:
                payload = json.loads(body.decode('utf-8', errors='ignore'))
            except json.JSONDecodeError as e:
                logger.error("nextcloud_talk webhook: invalid JSON payload")
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error":"Invalid JSON"}')
                return

            # Normalize backend URL
            normalized_backend = extract_backend_url(backend)

            # Process payload
            processed = self._process_payload(payload, normalized_backend)

            # Return 200 OK
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        except Exception:
            logger.exception("nextcloud_talk webhook: handling failed")
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'{"error":"Internal server error"}')

    def _process_payload(self, payload: dict, backend_url: str) -> bool:
        """
        Process the Activity Streams payload and enqueue for processing.

        Returns True if message was enqueued, False otherwise.
        """
        # Extract activity type - handle both direct and nested structures
        activity_type = payload.get("type", "")
        
        # Extract object first (needed for subsequent checks)
        obj = payload.get("object", {})
        
        # Some Nextcloud versions wrap the real activity in an "Activity" type
        # If the type is "Activity", try to extract the real activity from object or meta
        if activity_type == "Activity":
            logger.debug("Detected wrapped Activity type, checking for nested structure")
            # Try to get actual activity from object's summary or other fields
            obj_summary = obj.get("summary", "")
            if obj_summary:
                logger.debug(f"Activity summary: {obj_summary}")
            
            # Log full object for debugging
            logger.info(f"Activity object name: {obj.get('name')}, type: {obj.get('type')}")
            logger.info(f"Activity object keys: {list(obj.keys())}")
            
            # Log content field which may contain file info
            obj_content = obj.get("content", "")
            if obj_content:
                logger.info(f"Activity object content (first 200 chars): {str(obj_content)[:200]}")
                # Try to parse as JSON to see if it contains file data
                try:
                    import json as json_lib
                    content_json = json_lib.loads(obj_content)
                    logger.info(f"Activity content parsed: {list(content_json.keys()) if isinstance(content_json, dict) else type(content_json)}")
                    if "parameters" in content_json:
                        logger.info(f"Activity has parameters: {list(content_json['parameters'].keys())}")
                except Exception as e:
                    logger.debug(f"Content is not JSON: {e}")

        # Extract actor information
        actor = payload.get("actor", {})
        actor_id, actor_name, actor_type = NextcloudTalkContentParser.parse_actor(
            actor
        )

        # Extract target (conversation)
        target = payload.get("target", {})
        conversation_token, conversation_name = (
            NextcloudTalkContentParser.parse_conversation(target)
        )
        
        # 添加详细日志用于调试
        logger.info(
            f"nextcloud_talk webhook: activity={activity_type} "
            f"actor={actor_id[:20]}... type={actor_type} "
            f"conversation={conversation_name[:30] if len(conversation_name) > 30 else conversation_name}"
        )
        logger.debug(f"Full payload keys: {list(payload.keys())}")
        logger.debug(f"Object details: name={obj.get('name')}, content={str(obj.get('content', ''))[:100]}")
        logger.debug(f"Full payload preview: {json.dumps(payload, indent=2)[:500]}")

        # 检查对话事件（bot added/removed）
        conversation_event = NextcloudTalkContentParser.extract_conversation_event(
            payload
        )
        if conversation_event:
            logger.info(f"nextcloud_talk webhook: bot {conversation_event} to conversation")
            return False  # 不需要处理

        # 检查反应
        reaction_data = NextcloudTalkContentParser.extract_reaction(payload)
        if reaction_data:
            emoji, message_id = reaction_data
            logger.info(f"nextcloud_talk webhook: reaction emoji={emoji} message_id={message_id}")
            return False  # 不处理反应

        # 检查多媒体文件（图片、视频、音频）
        media_info = NextcloudTalkContentParser.extract_media_file(payload)
        if media_info:
            logger.info(
                f"nextcloud_talk webhook: media file type={media_info['type']} "
                f"name={media_info['name']} size={media_info['size']}"
            )

            # 导入 webdav 工具函数
            from .webdav import build_webdav_url
            from .utils import extract_backend_url

            # Normalize backend_url (remove /s/... suffix)
            backend_url = extract_backend_url(backend_url)

            # 尝试构建 WebDAV URL（如果 api_user 可用）
            webdav_url = None
            if self._api_user and media_info.get("path"):
                # Try to extract filename from metadata
                metadata = media_info.get("metadata", {})
                filename = media_info.get("name", metadata.get("name", ""))
                file_path = media_info.get("path", "")

                logger.info(f"Building WebDAV URL: base_url={backend_url}, api_user={self._api_user}, file_path={file_path}, filename={filename}")

                # Build WebDAV URL with file_path and filename
                webdav_url = build_webdav_url(
                    backend_url, 
                    self._api_user, 
                    file_path
                )
                if webdav_url:
                    logger.info(f"Built WebDAV URL: {webdav_url}")
                else:
                    logger.warning(f"Failed to build WebDAV URL for path: {file_path}, api_user: {self._api_user}")

            # Extract share link from metadata for agent to access the file
            metadata = media_info.get("metadata", {})
            share_link = metadata.get("share-token") or metadata.get("link")

            # 优先使用 WebDAV URL，fallback 到分享链接
            download_url = webdav_url
            if not download_url and share_link:
                download_url = share_link

            file_content_part = {
                "type": "file",
                "file_type": media_info["type"],
                "file_name": media_info["name"],
                "filename": media_info["name"],  # Alternative name
                "file_path": media_info["path"],
                "filepath": media_info["path"],  # Alternative path
                "mime_type": media_info["mime_type"],
                "mimetype": media_info["mime_type"],  # Alternative mime type
                "size": media_info["size"],
                "preview_available": media_info["preview_available"],
                "metadata": {
                    **metadata,
                    "webdav_url": webdav_url,  # Add WebDAV URL to metadata
                },
                # Add source field for file download in message processing
                # Use WebDAV URL if available, otherwise fall back to share link
                "source": {
                    "type": "url",
                    "url": download_url
                } if download_url else {"type": "unknown"},
            }
            
            channel_payload = {
                "channel_id": "nextcloud_talk",
                "sender_id": actor_id,
                "session_webhook": backend_url,
                "content_parts": [file_content_part],
                "meta": {
                    "actor_id": actor_id,
                    "actor_name": actor_name,
                    "actor_type": actor_type,
                    "conversation_token": conversation_token,
                    "conversation_name": conversation_name,
                    "message_id": obj.get("id", ""),
                    "backend_url": backend_url,
                    "bot_prefix": self._bot_prefix,
                    "media_info": media_info,
                    "api_user": self._api_user,  # Bot account for WebDAV access
                    "webdav_url": webdav_url,  # WebDAV URL for direct download
                    "original_content_parts": [file_content_part],  # Preserve original dict data
                },
            }
            
            # 入队
            logger.info(f"Checking enqueue callback: {self._enqueue_callback is not None}")
            if self._enqueue_callback:
                logger.info(f"Enqueueing media file: {media_info['name']}")
                # Schedule the async callback in the main event loop
                import asyncio
                try:
                    # 获取主事件循环（由应用启动时创建）
                    loop = asyncio.get_running_loop()
                    # 在主线程的事件循环中调度协程
                    asyncio.run_coroutine_threadsafe(
                        self._enqueue_callback(channel_payload),
                        loop
                    )
                except RuntimeError:
                    # 如果没有运行中的事件循环，在新线程中运行
                    def run_in_thread():
                        asyncio.run(self._enqueue_callback(channel_payload))
                    import threading
                    thread = threading.Thread(target=run_in_thread, daemon=True)
                    thread.start()
                return True
            else:
                logger.warning("nextcloud_talk webhook: no enqueue callback set")
                return False
        else:
            # 添加调试日志，查看 payload 结构
            logger.debug(f"Not a media file. activity_type={payload.get('type')}, object_name={obj.get('name')}")

        # 检查普通消息
        message = NextcloudTalkContentParser.extract_message_text(payload)
        if message is None:
            logger.debug("nextcloud_talk webhook: not a regular message, ignoring")
            return False

        # 构建用于 channel 处理的 payload
        channel_payload = {
            "channel_id": "nextcloud_talk",
            "sender_id": actor_id,
            "session_webhook": backend_url,
            "content_parts": [{"type": "text", "text": message}],
            "meta": {
                "actor_id": actor_id,
                "actor_name": actor_name,
                "actor_type": actor_type,
                "conversation_token": conversation_token,
                "conversation_name": conversation_name,
                "message_id": obj.get("id", ""),
                "backend_url": backend_url,
                "bot_prefix": self._bot_prefix,
            },
        }

        logger.info(f"nextcloud_talk webhook: enqueueing message from {actor_name}")

        # 入队
        if self._enqueue_callback:
            self._enqueue_callback(channel_payload)
            return True
        else:
            logger.warning("nextcloud_talk webhook: no enqueue callback set")
            return False


class StdlibWebhookServer:
    """
    Webhook server using Python standard library http.server.
    
    Runs in a separate thread to avoid blocking the main event loop.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8765,
        webhook_path: str = "/webhook/nextcloud_talk",
    ):
        """
        Initialize server.
        
        Args:
            host: Server host (default "0.0.0.0" for all interfaces)
            port: Server port (default 8765)
            webhook_path: Webhook endpoint path
        """
        self.host = host
        self.port = port
        self.webhook_path = webhook_path
        
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def set_enqueue_callback(self, callback: Callable):
        """Set the enqueue callback for the handler."""
        NextcloudTalkWebhookHandler._enqueue_callback = callback

    def set_webhook_secret(self, secret: str):
        """Set the webhook secret for signature verification."""
        NextcloudTalkWebhookHandler._webhook_secret = secret

    def set_bot_prefix(self, prefix: str):
        """Set the bot message prefix."""
        NextcloudTalkWebhookHandler._bot_prefix = prefix

    def set_api_user(self, api_user: str):
        """Set the bot API user for WebDAV access."""
        NextcloudTalkWebhookHandler._api_user = api_user

    def start(self):
        """Start the webhook server in a background thread."""
        if self._server is not None:
            logger.warning("nextcloud_talk webhook server already running")
            return

        def run_server():
            self._server = HTTPServer((self.host, self.port), NextcloudTalkWebhookHandler)
            logger.info(
                f"nextcloud_talk webhook server listening on {self.host}:{self.port}"
            )
            self._server.serve_forever()

        self._stop_event.clear()
        self._thread = threading.Thread(target=run_server, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the webhook server."""
        if self._server is None:
            return

        self._stop_event.set()
        self._server.shutdown()
        self._server.server_close()
        self._server = None

        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

        logger.info("nextcloud_talk webhook server stopped")
