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

    def __init__(self, *args, **kwargs):
        """
        Initialize handler.

        Class-level attributes (set by channel):
        - _enqueue_callback: Function to enqueue payloads to channel
        - _webhook_secret: Shared secret for signature verification
        - _bot_prefix: Bot message prefix
        """
        self._enqueue_callback = self.__class__._enqueue_callback
        self._webhook_secret = self.__class__._webhook_secret
        self._bot_prefix = self.__class__._bot_prefix
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
        # Extract activity type
        activity_type = payload.get("type", "")

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

        # Extract object
        obj = payload.get("object", {})

        logger.info(
            f"nextcloud_talk webhook: activity={activity_type} "
            f"actor={actor_id[:20]}... type={actor_type} "
            f"conversation={conversation_name[:30] if len(conversation_name) > 30 else conversation_name}"
        )

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
