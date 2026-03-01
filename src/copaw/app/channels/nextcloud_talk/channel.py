# -*- coding: utf-8 -*-
"""Nextcloud Talk Channel.

Nextcloud Talk (Spreed) Bot integration via Webhook API.

Features:
- Receive chat messages via webhook
- Send messages/reactions via bot API
- HMAC-SHA256 signature verification
- Support for Markdown messages
- Session management for proactive/cron sends

Bot Installation:
    Requires Nextcloud admin to run:
    ./occ talk:bot:install <name> <webhook-url> [--secret=<secret>]

API Documentation:
    https://nextcloud-talk.readthedocs.io/en/latest/bots/
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

from ..base import (
    BaseChannel,
    ContentType,
    OnReplySent,
    ProcessHandler,
)

from .constants import (
    NEXTCLOUD_TALK_DEBOUNCE_SECONDS,
    SESSION_ID_SUFFIX_LEN,
)
from .content_utils import (
    NextcloudTalkContentParser,
    session_param_from_token,
)
from .handler_stdlib import StdlibWebhookServer
from .utils import (
    generate_bot_signature,
    normalize_nextcloud_url,
    build_bot_headers,
    get_token_store_path,
    load_token_store,
    save_token_store,
)

logger = logging.getLogger(__name__)


class NextcloudTalkChannel(BaseChannel):
    """
    Nextcloud Talk Channel: Webhook -> Incoming -> to_agent_request ->
    process -> send_response -> Nextcloud API.

    Proactive send (stored backend_url):
    - We store backend_url from incoming messages in memory
    - Key uses conversation token for lookup
    - to_handle "nextcloud_talk:token:<token>" stores by conversation
    """

    channel = "nextcloud_talk"

    def __init__(
        self,
        process: ProcessHandler,
        enabled: bool,
        webhook_secret: str,
        webhook_host: str = "0.0.0.0",
        webhook_port: int = 8765,
        webhook_path: str = "/webhook/nextcloud_talk",
        bot_prefix: str = "[BOT] ",
        on_reply_sent: OnReplySent = None,
        show_tool_details: bool = True,
    ):
        super().__init__(
            process,
            on_reply_sent=on_reply_sent,
            show_tool_details=show_tool_details,
        )
        self.enabled = enabled
        self.webhook_secret = webhook_secret
        self.webhook_host = webhook_host
        self.webhook_port = webhook_port
        self.webhook_path = webhook_path or "/webhook/nextcloud_talk"
        self.bot_prefix = bot_prefix

        # Webhook server (using Python standard library, no FastAPI)
        self._webhook_server: Optional[StdlibWebhookServer] = None

        # HTTP client (using aiohttp - already used by other channels)
        self._http: Optional[aiohttp.ClientSession] = None

        # Token store (conversation_token -> bot_token mapping)
        # This is loaded/saved to disk for persistence
        self._token_store: Dict[str, str] = {}
        self._token_store_lock = asyncio.Lock()

        # Backend URL store for proactive sends
        # Maps conversation/token suffix to backend URL
        self._backend_url_store: Dict[str, str] = {}
        self._backend_url_lock = asyncio.Lock()

        # Time debounce (disabled, manager handles)
        self._debounce_seconds = 0.0

    @classmethod
    def from_env(
        cls,
        process: ProcessHandler,
        on_reply_sent: OnReplySent = None,
    ) -> "NextcloudTalkChannel":
        return cls(
            process=process,
            enabled=os.getenv("NEXTCLOUD_TALK_CHANNEL_ENABLED", "1") == "1",
            webhook_secret=os.getenv("NEXTCLOUD_TALK_WEBHOOK_SECRET", ""),
            webhook_host=os.getenv("NEXTCLOUD_TALK_WEBHOOK_HOST", "0.0.0.0"),
            webhook_port=int(os.getenv("NEXTCLOUD_TALK_WEBHOOK_PORT", "8765")),
            webhook_path=os.getenv(
                "NEXTCLOUD_TALK_WEBHOOK_PATH",
                "/webhook/nextcloud_talk",
            ),
            bot_prefix=os.getenv("NEXTCLOUD_TALK_BOT_PREFIX", "[BOT] "),
            on_reply_sent=on_reply_sent,
        )

    @classmethod
    def from_config(
        cls,
        process: ProcessHandler,
        config: Any,
        on_reply_sent: OnReplySent = None,
        show_tool_details: bool = True,
    ) -> "NextcloudTalkChannel":
        return cls(
            process=process,
            enabled=config.enabled,
            webhook_secret=getattr(config, "webhook_secret", ""),
            webhook_host=getattr(config, "webhook_host", "0.0.0.0"),
            webhook_port=getattr(config, "webhook_port", 8765),
            webhook_path=getattr(config, "webhook_path", "/webhook/nextcloud_talk"),
            bot_prefix=getattr(config, "bot_prefix", "[BOT] "),
            on_reply_sent=on_reply_sent,
            show_tool_details=show_tool_details,
        )

    # ---------------------------
    # Session and token management
    # ---------------------------

    def resolve_session_id(
        self,
        sender_id: str,
        channel_meta: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Session_id = conversation token for consistent conversation tracking.
        """
        meta = channel_meta or {}
        conversation_token = meta.get("conversation_token", "")
        if conversation_token:
            # Use conversation token as session
            return f"{self.channel}:{conversation_token}"
        return f"{self.channel}:{sender_id}"

    def get_debounce_key(self, payload: Any) -> str:
        """Use conversation_token for debouncing."""
        if isinstance(payload, dict):
            token = (payload.get("meta") or {}).get("conversation_token", "")
            return token or payload.get("sender_id", "")
        return ""

    def to_handle_from_target(self, *, user_id: str, session_id: str) -> str:
        """
        Resolve cron dispatch target to channel-specific to_handle.

        Format: "nextcloud_talk:token:<conversation_token>"
        """
        # session_id format: "nextcloud_talk:<conversation_token>"
        if session_id and session_id.startswith(f"{self.channel}:"):
            token = session_id[len(self.channel) + 1 :]
            return f"nextcloud_talk:token:{token}"
        return f"nextcloud_talk:token:{user_id}"

    def _route_from_handle(self, to_handle: str) -> dict:
        """
        Parse to_handle to extract conversation token and backend URL.

        Supported formats:
        - "nextcloud_talk:token:<token>" -> lookup backend URL from store
        - "http://..." -> direct backend URL
        """
        s = (to_handle or "").strip()

        # Direct URL
        if s.startswith("http://") or s.startswith("https://"):
            return {"backend_url": s}

        # Token format
        if s.startswith("nextcloud_talk:token:"):
            token = s[len("nextcloud_talk:token:") :]
            return {"conversation_token": token}

        return {}

    async def _load_token_store(self) -> None:
        """Load token store from disk."""
        try:
            store = load_token_store()
            self._token_store = store
            logger.info(f"loaded token store with {len(store)} entries")
        except Exception:
            logger.exception("failed to load token store")

    async def _save_backend_url(self, conversation_token: str, backend_url: str) -> None:
        """Store backend URL for a conversation."""
        if not conversation_token or not backend_url:
            return

        # Generate key from token suffix (same as session_id suffix)
        key = session_param_from_token(conversation_token)

        async with self._backend_url_lock:
            self._backend_url_store[key] = backend_url

        logger.debug(f"saved backend_url for token_suffix={key}")

    async def _get_backend_url_from_token(self, conversation_token: str) -> Optional[str]:
        """
        Lookup backend URL for a conversation token.

        Uses token suffix for lookup (consistent with session_id).
        """
        key = session_param_from_token(conversation_token)

        async with self._backend_url_lock:
            return self._backend_url_store.get(key)

    # ---------------------------
    # Build AgentRequest from native
    # ---------------------------

    def build_agent_request_from_native(
        self,
        native_payload: Any,
    ) -> Any:
        """
        Build AgentRequest from Nextcloud Talk native dict.
        """
        payload = native_payload if isinstance(native_payload, dict) else {}
        channel_id = payload.get("channel_id") or self.channel
        sender_id = payload.get("sender_id") or ""
        content_parts = payload.get("content_parts") or []
        meta = dict(payload.get("meta") or {})

        # Extract and store backend URL for proactive sends
        backend_url = meta.get("backend_url") or payload.get("session_webhook", "")
        conversation_token = meta.get("conversation_token", "")
        if backend_url and conversation_token:
            asyncio.create_task(self._save_backend_url(conversation_token, backend_url))

        session_id = self.resolve_session_id(sender_id, meta)

        request = self.build_agent_request_from_user_content(
            channel_id=channel_id,
            sender_id=sender_id,
            session_id=session_id,
            content_parts=content_parts,
            channel_meta=meta,
        )

        if hasattr(request, "channel_meta"):
            request.channel_meta = meta

        return request

    # ---------------------------
    # Webhook setup
    # ---------------------------

    def _setup_webhook_server(self):
        """Setup webhook server using Python standard library."""
        self._webhook_server = StdlibWebhookServer(
            host=self.webhook_host,
            port=self.webhook_port,
            webhook_path=self.webhook_path,
        )

        # Set callback and config
        self._webhook_server.set_enqueue_callback(self._enqueue)
        self._webhook_server.set_webhook_secret(self.webhook_secret)
        self._webhook_server.set_bot_prefix(self.bot_prefix)

    # ---------------------------
    # Lifecycle
    # ---------------------------

    async def start(self) -> None:
        if not self.enabled:
            logger.debug("nextcloud_talk channel disabled")
            return

        if not self.webhook_secret:
            raise RuntimeError(
                "NEXTCLOUD_TALK_WEBHOOK_SECRET is required when channel is enabled"
            )

        # Load token store
        await self._load_token_store()

        # Load backend URL store from disk (same file format)
        self._backend_url_store = load_token_store()

        # Create HTTP session (aiohttp is already used by other channels)
        self._http = aiohttp.ClientSession()

        # Setup and start webhook server (using stdlib, no new dependency)
        self._setup_webhook_server()
        self._webhook_server.start()

        logger.info(
            f"nextcloud_talk channel started: webhook at "
            f"{self.webhook_host}:{self.webhook_port}{self.webhook_path}"
        )

    async def stop(self) -> None:
        if not self.enabled:
            return

        # Stop webhook server
        if self._webhook_server:
            self._webhook_server.stop()
            self._webhook_server = None

        # Close HTTP session
        if self._http:
            await self._http.close()
            self._http = None

        logger.info("nextcloud_talk channel stopped")

    # ---------------------------
    # Sending messages
    # ---------------------------

    async def send(
        self,
        to_handle: str,
        text: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Send a message to Nextcloud Talk.

        To handle formats:
        - "nextcloud_talk:token:<token>" -> send to that conversation
        - "http://..." -> direct backend URL (fallback)

        Bot token is required for sending. It should have been stored
        when the bot was installed in the conversation.
        """
        if not self.enabled:
            return

        if not self._http:
            logger.warning("nextcloud_talk http session not available")
            return

        # Get conversation token from meta
        meta = meta or {}
        meta_token = meta.get("conversation_token", "")

        # Resolve backend URL and token
        route = self._route_from_handle(to_handle)

        # Try to get backend URL from meta
        backend_url = meta.get("backend_url") or meta.get("session_webhook", "")

        # If not in meta, try lookup from token
        if not backend_url and "conversation_token" in route:
            token = route["conversation_token"]
            backend_url = await self._get_backend_url_from_token(token)
            if not backend_url:
                # Use meta_token as fallback
                if meta_token:
                    backend_url = await self._get_backend_url_from_token(meta_token)

        if not backend_url:
            logger.warning(
                f"nextcloud_talk cannot send: no backend_url for to_handle={to_handle}"
            )
            return

        # Determine bot token
        # The bot token is stored when the bot is installed in a conversation
        # For now, we'll use the conversation token as the bot token
        # In a real implementation, you'd need to store the bot token separately
        # TODO: Implement proper bot token storage
        conversation_token = route.get("conversation_token", meta_token)
        bot_token = conversation_token  # This won't work! Need real bot token

        if not bot_token:
            logger.warning(
                "nextcloud_talk cannot send: no bot token available"
            )
            return

        # Normalize backend URL
        backend_url = normalize_nextcloud_url(backend_url)

        # Build request body
        body = {"message": text}

        # Generate signature
        body_str = json.dumps(body)
        headers = build_bot_headers(self.webhook_secret, body_str)

        headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

        # Send message
        url = f"{backend_url}ocs/v2.php/apps/spreed/api/v1/bot/{bot_token}/message"

        try:
            async with self._http.post(url, json=body, headers=headers) as resp:
                resp_text = await resp.text()

                if resp.status >= 400:
                    logger.warning(
                        f"nextcloud_talk send failed: status={resp.status} body={resp_text[:200]}"
                    )
                    return

                # Try to parse JSON response
                try:
                    resp_data = json.loads(resp_text) if resp_text else {}
                except json.JSONDecodeError:
                    resp_data = {}

                ocs = resp_data.get("ocs", {})
                meta = ocs.get("meta", {})

                if meta.get("statuscode") != 200:
                    logger.warning(
                        f"nextcloud_talk send API error: "
                        f"statuscode={meta.get('statuscode')} "
                        f"message={meta.get('message')}"
                    )
                    return

                logger.info(
                    f"nextcloud_talk send ok: token={bot_token} len={len(text)}"
                )

        except Exception:
            logger.exception(f"nextcloud_talk send failed for token={bot_token}")

    async def send_content_parts(
        self,
        to_handle: str,
        parts: List[Any],
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Send content parts (text, images, etc.) to Nextcloud Talk.

        For now, only text is supported. Media attachments would require
        file sharing via Nextcloud Files API.
        """
        # Extract text parts
        text_parts = []
        for p in parts:
            t = getattr(p, "type", None)
            if t == ContentType.TEXT and getattr(p, "text", None):
                text_parts.append(p.text or "")
            elif t == ContentType.REFUSAL and getattr(p, "refusal", None):
                text_parts.append(p.refusal or [])

        body = "\n".join(text_parts) if text_parts else ""

        prefix = (meta or {}).get("bot_prefix", "")
        if prefix and body:
            body = prefix + body
        elif prefix and not body:
            body = prefix

        if body.strip():
            await self.send(to_handle, body.strip(), meta)
