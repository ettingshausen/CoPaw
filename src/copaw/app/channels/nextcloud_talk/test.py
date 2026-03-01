#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Test script for Nextcloud Talk channel.
Verifies imports and basic functionality.
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=" * 60)
print("Nextcloud Talk Channel Test")
print("=" * 60)

# Test 1: Import channel
print("\n[1/5] Testing imports...")
try:
    from copaw.app.channels.nextcloud_talk import NextcloudTalkChannel
    from copaw.app.channels.nextcloud_talk.constants import (
        HEADER_SIGNATURE,
        HEADER_RANDOM,
        SIGNATURE_LENGTH,
    )
    from copaw.app.channels.nextcloud_talk.utils import (
        verify_request_signature,
        generate_bot_signature,
    )
    from copaw.app.channels.nextcloud_talk.content_utils import (
        NextcloudTalkContentParser,
    )
    from copaw.app.channels.nextcloud_talk.handler_stdlib import (
        StdlibWebhookServer,
    )
    print("✅ All imports successful")
except Exception as e:
    print(f"❌ Import failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test 2: Verify constants
print("\n[2/5] Testing constants...")
assert HEADER_SIGNATURE == "X-Nextcloud-Talk-Signature"
assert HEADER_RANDOM == "X-Nextcloud-Talk-Random"
assert SIGNATURE_LENGTH == 64
print(f"✅ Constant HEADER_SIGNATURE = {HEADER_SIGNATURE}")
print(f"✅ Constant HEADER_RANDOM = {HEADER_RANDOM}")
print(f"✅ Constant SIGNATURE_LENGTH = {SIGNATURE_LENGTH}")

# Test 3: Signature generation and verification
print("\n[3/5] Testing signature generation and verification...")
secret = "test_secret_123"
body = '{"message": "hello"}'
random_val, signature = generate_bot_signature(body, secret)
print(f"✅ Generated random: {random_val[:16]}...")
print(f"✅ Generated signature: {signature[:16]}...")

# Verify
body_bytes = body.encode("utf-8")
is_valid = verify_request_signature(body_bytes, signature, random_val, secret)
assert is_valid, "Signature verification failed"
print("✅ Signature verification successful")

# Test 4: Content parsing
print("\n[4/5] Testing content parsing...")
sample_payload = {
    "type": "Create",
    "actor": {
        "type": "Person",
        "id": "users/testuser",
        "name": "Test User"
    },
    "object": {
        "type": "Note",
        "id": "123",
        "name": "message",
        "content": '{"message":"hello world!","parameters":{}}',
        "mediaType": "text/markdown"
    },
    "target": {
        "type": "Collection",
        "id": "conv-token-abc123",
        "name": "Test Chat"
    }
}

message = NextcloudTalkContentParser.extract_message_text(sample_payload)
assert message == "hello world!", f"Expected 'hello world!', got '{message}'"
print(f"✅ Extracted message: {message}")

# Parse participant
actor_id, actor_name, actor_type = NextcloudTalkContentParser.parse_actor(
    sample_payload["actor"]
)
assert actor_name == "Test User", f"Expected 'Test User', got '{actor_name}'"
print(f"✅ Parsed actor: {actor_name} ({actor_type})")

# Test 5: Channel class
print("\n[5/5] Testing channel class...")
def dummy_process(request):
    async def gen():
        yield None
    return gen()

try:
    # Note: We can't fully initialize the channel without a real config,
    # but we can verify the class structure
    assert hasattr(NextcloudTalkChannel, "channel")
    assert NextcloudTalkChannel.channel == "nextcloud_talk"
    assert hasattr(NextcloudTalkChannel, "start")
    assert hasattr(NextcloudTalkChannel, "stop")
    assert hasattr(NextcloudTalkChannel, "send")
    print("✅ Channel class structure verified")
    print(f"✅ Channel name: {NextcloudTalkChannel.channel}")
except Exception as e:
    print(f"❌ Channel class test failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n" + "=" * 60)
print("✅ All tests passed!")
print("=" * 60)

print("\nNextcloud Talk channel is ready to use.")
print("\nPlease ensure:")
print("  1. Configure webhook_secret in config.json")
print("  2. Install bot in Nextcloud using OCC commands")
print("  3. Set up reverse proxy with X-Nextcloud-Talk-Backend header")
print("\nNo additional dependencies needed - uses Python standard library!")
