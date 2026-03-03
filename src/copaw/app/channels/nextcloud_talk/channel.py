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
    - We store backend_url from incoming messages in memory and disk
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
        
        # Log secret length for debugging (don't log the actual secret)
        logger.info(
            f"nextcloud_talk channel initialized: "
            f"enabled={enabled} secret_len={len(webhook_secret)} "
            f"host={webhook_host} port={webhook_port}"
        )
        self.webhook_host = webhook_host
        self.webhook_port = webhook_port
        self.webhook_path = webhook_path or "/webhook/nextcloud_talk"
        self.bot_prefix = bot_prefix

        # Webhook server (using Python standard library, no FastAPI)
        self._webhook_server: Optional[StdlibWebhookServer] = None

        # HTTP client (using aiohttp - already used by other channels)
        self._http: Optional[aiohttp.ClientSession] = None

        # Backend URL store for proactive sends
        # Maps conversation/token suffix to backend URL for cron jobs
        self._backend_url_store: Dict[str, str] = {}
        self._backend_url_lock = asyncio.Lock()

        # Time debounce (disabled, manager handles)
        self._debounce_seconds = 0.0
        
        # Rate limiting: track last send time per session
        self._last_send_time: Dict[str, float] = {}
        self._send_lock = asyncio.Lock()
        
        # Minimum interval between sends (seconds)
        self._min_send_interval = 2.0

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
    # Session and backend URL management
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

        # Load backend URL store from disk
        self._backend_url_store = load_token_store() or {}

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

        # Persist the backend URL store for cron jobs
        if self._backend_url_store:
            try:
                save_token_store(self._backend_url_store)
                logger.info(f"persisted backend_url_store with {len(self._backend_url_store)} entries")
            except Exception:
                logger.exception("failed to persist backend_url_store")

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

        NOTE: This endpoint expects the webhook_secret to be configured
        in the config.json file. The bot must use the same secret that
        was used during installation.

        The backend URL and conversation token must be properly configured.
        """
        if not self.enabled:
            return

        if not self._http:
            logger.warning("nextcloud_talk http session not available")
            return

        # Rate limiting: ensure minimum interval between sends
        session_id = to_handle
        async with self._send_lock:
            last_time = self._last_send_time.get(session_id, 0)
            current_time = asyncio.get_event_loop().time()
            elapsed = current_time - last_time
            
            if elapsed < self._min_send_interval:
                delay = self._min_send_interval - elapsed
                logger.debug(f"nextcloud_talk rate limiting: waiting {delay:.1f}s before sending")
                await asyncio.sleep(delay)
            
            self._last_send_time[session_id] = asyncio.get_event_loop().time()

        # Truncate message if too long (Nextcloud Talk has message length limits)
        max_length = 4000  # Reasonable limit for chat messages
        if len(text) > max_length:
            text = text[:max_length - 3] + "..."
            logger.warning(f"nextcloud_talk message truncated to {max_length} chars")

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

        # Get conversation token (for API request)
        conversation_token = route.get("conversation_token", meta_token)

        if not conversation_token:
            logger.warning(
                f"nextcloud_talk cannot send: no conversation token for to_handle={to_handle}"
            )
            return

        # Normalize backend URL
        backend_url = normalize_nextcloud_url(backend_url)

        # Build request body following Nextcloud Talk Bot API spec:
        # { "message": "<text>" }
        # Note: conversation token is in the URL path, not body
        body = {
            "message": text,
        }
        body_str = json.dumps(body, ensure_ascii=False, separators=(',', ':'))

        # Generate signature on the message text (not full JSON body)
        # According to Nextcloud Talk Bot API verification logic:
        # signature = HMAC(random_header + message_text, secret)
        headers = build_bot_headers(self.webhook_secret, text)

        headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

        # Send message
        url = f"{backend_url}ocs/v2.php/apps/spreed/api/v1/bot/{conversation_token}/message"

        # Debug logging - show full values for verification
        random_val = headers.get('X-Nextcloud-Talk-Bot-Random', 'N/A')
        signature_val = headers.get('X-Nextcloud-Talk-Bot-Signature', 'N/A')
        logger.info(
            f"nextcloud_talk send: url={url} "
            f"secret_len={len(self.webhook_secret)} "
            f"random={random_val} "
            f"signature={signature_val} "
            f"body={body_str}"
        )

        try:
            # Use data=body_str to ensure exact same body is sent (for signature verification)
            async with self._http.post(url, data=body_str.encode('utf-8'), headers=headers) as resp:
                resp_text = await resp.text()

                if resp.status >= 400:
                    logger.warning(
                        f"nextcloud_talk send failed: status={resp.status} "
                        f"url={url} "
                        f"secret_len={len(self.webhook_secret)} "
                        f"body={resp_text[:200]}"
                    )
                    return

                # Try to parse JSON response
                try:
                    resp_data = json.loads(resp_text) if resp_text else {}
                except json.JSONDecodeError:
                    resp_data = {}

                ocs = resp_data.get("ocs", {})
                resp_meta = ocs.get("meta", {})

                if resp_meta.get("statuscode") != 200:
                    logger.warning(
                        f"nextcloud_talk send API error: "
                        f"statuscode={resp_meta.get('statuscode')} "
                        f"message={resp_meta.get('message')}"
                    )
                    return

                logger.info(
                    f"nextcloud_talk send ok: conversation={conversation_token} len={len(text)}"
                )

        except Exception:
            logger.exception(f"nextcloud_talk send failed for conversation={conversation_token}")

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
                text_parts.append(p.refusal or "")

        body = "\n".join(text_parts) if text_parts else ""

        prefix = (meta or {}).get("bot_prefix", "")
        if prefix and body:
            body = prefix + body
        elif prefix and not body:
            body = prefix

        if body.strip():
            await self.send(to_handle, body.strip(), meta)

    async def _run_process_loop(
        self,
        request: "AgentRequest",
        to_handle: str,
        send_meta: Dict[str, Any],
    ) -> None:
        """
        Override _run_process_loop to batch messages for rate limiting.
        Nextcloud Talk Bot API has rate limits, so we collect all messages
        and send them at the end as a single message.
        """
        from agentscope_runtime.engine.schemas.agent_schemas import RunStatus
        
        session_id = getattr(request, "session_id", "") or ""
        bot_prefix = send_meta.get("bot_prefix", "") or getattr(
            self, "bot_prefix", "",
        )
        
        # Collect all messages
        all_messages: List[str] = []
        last_response = None
        
        try:
            async for event in self._process(request):
                obj = getattr(event, "object", None)
                status = getattr(event, "status", None)
                
                if obj == "message" and status == RunStatus.Completed:
                    # Extract text from event
                    text = self._extract_text_from_event(event)
                    if text:
                        all_messages.append(text)
                        
                elif obj == "response":
                    last_response = event
                    await self.on_event_response(request, event)
            
            # Send all collected messages as a single message
            if all_messages:
                final_message = "\n\n".join(all_messages)
                if bot_prefix:
                    final_message = bot_prefix + final_message
                await self.send(to_handle, final_message, send_meta)
            
            if last_response and getattr(last_response, "error", None):
                err = getattr(
                    last_response.error,
                    "message",
                    str(last_response.error),
                )
                err_text = (bot_prefix or "") + f"Error: {err}"
                await self._on_consume_error(request, to_handle, err_text)
                
            if self._on_reply_sent:
                args = self.get_on_reply_sent_args(request, to_handle)
                self._on_reply_sent(self.channel, *args)
                
        except Exception:
            logger.exception("channel consume_one failed")
            await self._on_consume_error(
                request,
                to_handle,
                "An error occurred while processing your request.",
            )
    
    def _extract_text_from_event(self, event: Any) -> str:
        """Extract text content from a message event.
        
        For Nextcloud Talk, we filter out technical details like tool calls
        and only return the final user-facing message.
        """
        text_parts = []
        
        # Try to get content from event
        content = getattr(event, "content", None)
        if not content:
            return ""
        
        # Check if this is a tool call message (contains 🔧 or similar markers)
        full_text = ""
        for part in content:
            part_type = getattr(part, "type", None)
            if part_type == "text":
                text = getattr(part, "text", "")
                if text:
                    full_text += text
            elif part_type == "refusal":
                refusal = getattr(part, "refusal", "")
                if refusal:
                    full_text += refusal
        
        # Filter out technical messages (tool calls, errors, etc.)
        # Only keep messages that don't look like technical internals
        if full_text:
            # Skip tool call messages (contain 🔧 or specific patterns)
            if "🔧 **" in full_text and "**\n```" in full_text:
                return ""  # Skip tool call messages
            
            # Skip error messages that are internal
            if full_text.startswith("✅ **") and "Error:" in full_text:
                return ""  # Skip internal error messages
            
            # Skip thinking/planning messages
            if any(pattern in full_text for pattern in [
                "用户问", "我可以", "让我", "我需要", 
                "我应该", "我来", "思考一下", "计算一下"
            ]) and len(full_text) < 100:
                # This looks like internal thinking, skip it
                return ""
            
            return full_text
        
        return ""
