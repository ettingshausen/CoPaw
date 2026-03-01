# -*- coding: utf-8 -*-
"""Content parsing utilities for Nextcloud Talk channel."""

import json
import logging
from typing import Any, Dict, Optional, Tuple

from .constants import (
    ACTIVITY_TYPE_CREATE,
    ACTIVITY_TYPE_JOIN,
    ACTIVITY_TYPE_LEAVE,
    ACTIVITY_TYPE_LIKE,
    ACTIVITY_TYPE_UNDO,
    ACTOR_TYPE_PERSON,
    ACTOR_TYPE_APPLICATION,
    OBJECT_TYPE_NOTE,
    MESSAGE_NAME_NORMAL,
    MEDIA_TYPE_MARKDOWN,
    MEDIA_TYPE_PLAIN,
)

logger = logging.getLogger(__name__)


class NextcloudTalkContentParser:
    """Parser for Nextcloud Talk Activity Streams 2.0 messages."""

    @staticmethod
    def parse_actor(actor: Dict[str, Any]) -> Tuple[str, str, str]:
        """
        Parse actor from Activity Streams payload.

        Returns:
            Tuple of (actor_id, actor_name, actor_type)
            - actor_id: "users/username" or "bots/bot-id"
            - actor_name: Display name
            - actor_type: "user", "guest", "bot"
        """
        actor_id = actor.get("id", "")
        actor_name = actor.get("name", "")
        actor_type_raw = actor.get("type", "")

        # Extract agent type from ID
        if actor_type_raw == ACTOR_TYPE_APPLICATION:
            actor_type = "bot"
        elif actor_type_raw == ACTOR_TYPE_PERSON:
            if actor_id.startswith("users/"):
                actor_type = "user"
            elif actor_id.startswith("guests/"):
                actor_type = "guest"
            else:
                actor_type = "unknown"
        else:
            actor_type = "unknown"

        # Extract username from ID
        if "/" in actor_id:
            username = actor_id.split("/", 1)[1]
        else:
            username = actor_id

        logger.debug(
            f"parsed actor: id={actor_id} name={actor_name} type={actor_type}"
        )

        return (actor_id, actor_name, actor_type)

    @staticmethod
    def parse_message_content(
        content_str: str,
        media_type: str = MEDIA_TYPE_PLAIN,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Parse message content string.

        The content is a JSON-encoded string with "message" and "parameters" keys.
        Parameters contain rich object data for mentions, calls, files, etc.

        Returns:
            Tuple of (message_text, parameters_dict)
        """
        try:
            content_data = json.loads(content_str)
            message = content_data.get("message", "")
            parameters = content_data.get("parameters", {})
        except json.JSONDecodeError:
            # If not JSON, use content string directly
            message = content_str
            parameters = {}

        logger.debug(
            f"parsed message content: message_len={len(message)} "
            f"params_keys={list(parameters.keys())}"
        )

        return (message, parameters)

    @staticmethod
    def replace_mentions(text: str, parameters: Dict[str, Any]) -> str:
        """
        Replace placeholders in message text with actual mentions.

        Example: "hi {mention-call1}!" -> "hi @world!"
        """
        for key, value in parameters.items():
            placeholder = "{" + key + "}"
            if placeholder in text:
                if isinstance(value, dict):
                    name = value.get("name", "")
                    mention_type = value.get("type", "")
                    if mention_type == "call":
                        text = text.replace(placeholder, f"@{name}")
                    elif mention_type == "user":
                        text = text.replace(placeholder, f"@{name}")
                    elif mention_type == "guest":
                        text = text.replace(placeholder, f"@{name}")
                    else:
                        # Generic fallback
                        text = text.replace(placeholder, name)
                else:
                    text = text.replace(placeholder, str(value))

        return text

    @staticmethod
    def parse_conversation(target: Dict[str, Any]) -> Tuple[str, str]:
        """
        Parse conversation/target from payload.

        Returns:
            Tuple of (conversation_token, conversation_name)
        """
        conversation_token = target.get("id", "")
        conversation_name = target.get("name", "")

        return (conversation_token, conversation_name)

    @staticmethod
    def is_regular_message(object_data: Dict[str, Any]) -> bool:
        """
        Check if the object represents a regular user message (not system message).
        """
        object_name = object_data.get("name", "")

        # Handle bug fix: empty string for attachments before Nextcloud 33
        if object_name == MESSAGE_NAME_NORMAL:
            return True

        # Some system messages have specific names
        system_names = [
            "message_deleted",
            "message_modified",
            "conversation_renamed",
            "conversation_avatar_changed",
            "user_joined",
            "user_left",
            "file_shared",
            "password_set",
            "call_started",
            "call_ended",
        ]

        # If object_name is empty or matches system name pattern, it's not regular
        if not object_name or object_name in system_names:
            return False

        # If it's not explicitly a system message, assume it's regular
        return True

    @staticmethod
    def extract_message_text(payload: Dict[str, Any]) -> Optional[str]:
        """
        Extract message text from Activity Streams payload.

        Returns None if payload doesn't contain a regular message.
        """
        activity_type = payload.get("type")
        actor = payload.get("actor", {})
        obj = payload.get("object", {})

        # Only handle Create activities from persons (users)
        if activity_type != ACTIVITY_TYPE_CREATE:
            return None

        actor_type_raw = actor.get("type", "")
        if actor_type_raw != ACTOR_TYPE_PERSON:
            return None

        # Check if it's a regular message
        if not NextcloudTalkContentParser.is_regular_message(obj):
            return None

        # Extract content
        content_str = obj.get("content", "")
        media_type = obj.get("mediaType", MEDIA_TYPE_PLAIN)

        message, parameters = NextcloudTalkContentParser.parse_message_content(
            content_str, media_type
        )

        # Replace mentions
        message = NextcloudTalkContentParser.replace_mentions(message, parameters)

        return message if message else None

    @staticmethod
    def extract_reaction(payload: Dict[str, Any]) -> Optional[Tuple[str, str]]:
        """
        Extract reaction from Activity Streams payload.

        Returns:
            Tuple of (reaction_emoji, message_id) or None
        """
        activity_type = payload.get("type")

        if activity_type == ACTIVITY_TYPE_LIKE:
            # Reaction added
            emoji = payload.get("content", "")
            obj = payload.get("object", {})
            message_id = obj.get("id", "")
            return (emoji, message_id) if emoji and message_id else None
        elif activity_type == ACTIVITY_TYPE_UNDO:
            # Reaction removed
            obj = payload.get("object", {})
            if obj.get("type") == ACTIVITY_TYPE_LIKE:
                # The object being undone is a Like
                emoji = obj.get("content", "")
                target = obj.get("target", {})
                # In Undo, the original object is nested
                message_id = target.get("id", "") or obj.get("id", "")
                return (emoji, message_id) if emoji and message_id else None

        return None

    @staticmethod
    def extract_conversation_event(payload: Dict[str, Any]) -> Optional[str]:
        """
        Extract conversation event type (bot added/removed).

        Returns:
            "added" or "removed" or None
        """
        activity_type = payload.get("type")
        actor = payload.get("actor", {})

        # Check if actor is a bot (Application type with bots/ prefix)
        actor_type_raw = actor.get("type", "")
        actor_id = actor.get("id", "")

        if actor_type_raw != ACTOR_TYPE_APPLICATION:
            return None

        if not actor_id.startswith("bots/"):
            return None

        if activity_type == ACTIVITY_TYPE_JOIN:
            return "added"
        elif activity_type == ACTIVITY_TYPE_LEAVE:
            return "removed"

        return None


def parse_data_url(url: str) -> Tuple[Optional[bytes], Optional[str]]:
    """Parse data URL (data:mime/type;base64,encoded_data)."""
    if not url.startswith("data:"):
        return (None, None)

    try:
        # Parse data URL
        header, data = url.split(",", 1)
        # Remove "data:" prefix
        header = header[5:]

        # Extract mime type and encoding
        parts = header.split(";")
        mime_type = parts[0] if parts else "application/octet-stream"

        # Check if base64
        is_base64 = any(p.strip() == "base64" for p in parts)

        if is_base64:
            import base64
            decoded = base64.b64decode(data)
            return (decoded, mime_type)
        else:
            # URL encoded
            from urllib.parse import unquote
            decoded = unquote(data).encode("utf-8")
            return (decoded, mime_type)
    except Exception:
        logger.exception("Failed to parse data URL")
        return (None, None)


def session_param_from_token(token: str) -> str:
    """Extract session identifier from conversation token."""
    # token is typically an alphanumeric string, use suffix for session
    if len(token) > SESSION_ID_SUFFIX_LEN:
        return token[-SESSION_ID_SUFFIX_LEN:]
    return token


def is_public_url(url: Optional[str]) -> bool:
    """Check if URL is a public HTTP/HTTPS URL."""
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    return url.startswith("http://") or url.startswith("https://")


def guess_suffix_from_content(data: bytes) -> str:
    """Guess file suffix from content using magic bytes."""
    if not data:
        return ".bin"

    # Check common magic bytes
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    elif data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    elif data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return ".gif"
    elif data.startswith(b"RIFF") and len(data) > 8 and data[8:12] == b"WEBP":
        return ".webp"
    elif data.startswith(b"%PDF"):
        return ".pdf"
    elif data.startswith(b"PK\x03\x04"):
        return ".zip"
    elif data.startswith(b"\x1f\x8b"):
        return ".gz"

    return ".bin"
