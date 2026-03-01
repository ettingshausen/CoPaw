vSigning and Verifying Requests#
Messages are signed using the shared secret that is specified when installing a bot on the server. Create a HMAC with SHA256 over the RANDOM header and the request body using the shared secret. Only when the SIGNATURE matches the request should be accepted and handled.

Sample PHP code:

$digest = hash_hmac('sha256', $_SERVER['HTTP_X_NEXTCLOUD_TALK_RANDOM'] . file_get_contents('php://input'), $secret);

if (!hash_equals($digest, strtolower($_SERVER['HTTP_X_NEXTCLOUD_TALK_SIGNATURE']))) {
    exit;
}
Receiving chat messages#
Bot receives all the chat messages following the same signature/verification method.

Headers#
Header	Content type	Description
HTTP_X_NEXTCLOUD_TALK_SIGNATURE	[a-f0-9]{64}	SHA265 signature of the body
HTTP_X_NEXTCLOUD_TALK_RANDOM	[A-Za-z0-9+\]{64}	Random string used when signing the body
HTTP_X_NEXTCLOUD_TALK_BACKEND	URI	Base URL of the Nextcloud server sending the message
Content#
The content format follows the Activity Streams 2.0 Vocabulary.

Sample chat message#
{
    "type": "Create",
    "actor": {
        "type": "Person",
        "id": "users/ada-lovelace",
        "name": "Ada Lovelace"
    },
    "object": {
        "type": "Note",
        "id": "1567",
        "name": "message",
        "content": "{\"message\":\"hi {mention-call1} !\",\"parameters\":{\"mention-call1\":{\"type\":\"call\",\"id\":\"n3xtc10ud\",\"name\":\"world\",\"call-type\":\"group\",\"icon-url\":\"https:\\/\\/nextcloud.local\\/ocs\\/v2.php\\/apps\\/spreed\\/api\\/v1\\/room\\/n3xtc10ud\\/avatar\"}}}",
        "mediaType": "text/markdown"
    },
    "target": {
        "type": "Collection",
        "id": "n3xtc10ud",
        "name": "world"
    }
}
Explanation#
Path	Description
actor.id	One of the known attendee types followed by the / slash character and a unique identifier within the given type. For users it is the Nextcloud user ID, for guests and email invited guests a random hash value.
actor.name	The display name of the attendee sending the message.
actor.talkParticipantType	Optional: 🆕 Added in Talk 21. If applicable the attendee's participant type is provided. However this field can not be provided in some cases, e.g. bots.
object.id	The message ID of the given message on the origin server. It can be used to react or reply to the given message.
object.name	For normal written messages message, otherwise one of the known system message identifiers. Due to a bug this was set to an empty string for messages with attachments until Nextcloud 33 with Talk 23.
object.content	A JSON encoded dictionary with a message and parameters key. The message can include placeholders and the Rich Object parameters are rendered into it in the chat view.
object.mediaType	text/markdown when the message should be interpreted as Markdown. Otherwise text/plain.
object.inReplyTo	Optional: 🆕 Added in Talk 21. If applicable the parent message which was quoted in this message
object.inReplyTo.actor	Optional: 🆕 Added in Talk 21. Same data as the actor
object.inReplyTo.object	Optional: 🆕 Added in Talk 21. Same data as the object (but never contains inReplyTo)
target.id	The token of the conversation in which the message was posted. It can be used to react or reply to the given message.
target.name	The name of the conversation in which the message was posted.
Receiving reaction added#
🆕 Added in Talk 21. Bots with the reaction feature receive special hooks when a reaction was added to a chat message.

Headers#
Header	Content type	Description
HTTP_X_NEXTCLOUD_TALK_SIGNATURE	[a-f0-9]{64}	SHA265 signature of the body
HTTP_X_NEXTCLOUD_TALK_RANDOM	[A-Za-z0-9+\]{64}	Random string used when signing the body
HTTP_X_NEXTCLOUD_TALK_BACKEND	URI	Base URL of the Nextcloud server sending the message
Content#
The content format follows the Activity Streams 2.0 Vocabulary.

Sample reaction added message#
{
    "type": "Like",
    "actor": {
        "type": "Person",
        "id": "users/ada-lovelace",
        "name": "Ada Lovelace"
    },
    "object": {
        "type": "Note",
        "id": "1567",
        "name": "message",
        "content": "{\"message\":\"hi {mention-call1} !\",\"parameters\":{\"mention-call1\":{\"type\":\"call\",\"id\":\"n3xtc10ud\",\"name\":\"world\",\"call-type\":\"group\",\"icon-url\":\"https:\\/\\/nextcloud.local\\/ocs\\/v2.php\\/apps\\/spreed\\/api\\/v1\\/room\\/n3xtc10ud\\/avatar\"}}}",
        "mediaType": "text/markdown"
    },
    "target": {
        "type": "Collection",
        "id": "n3xtc10ud",
        "name": "world"
    },
    "content": "\ud83d\ude06"
}
Explanation#
Path	Description
actor.id	One of the known attendee types followed by the / slash character and a unique identifier within the given type. For users it is the Nextcloud user ID, for guests and email invited guests a random hash value.
actor.name	The display name of the attendee sending the message.
actor.talkParticipantType	Optional: If applicable the attendee's participant type is provided. However this field can not be provided in some cases, e.g. bots.
object.id	The message ID of the given message on the origin server. It can be used to react or reply to the given message.
object.name	For normal written messages message, otherwise one of the known system message identifiers.
object.content	A JSON encoded dictionary with a message and parameters key. The message can include placeholders and the Rich Object parameters are rendered into it in the chat view.
object.mediaType	text/markdown when the message should be interpreted as Markdown. Otherwise text/plain.
target.id	The token of the conversation in which the message was posted. It can be used to react or reply to the given message.
target.name	The name of the conversation in which the message was posted.
content	The reaction emoji that was added
Receiving reaction removed#
🆕 Added in Talk 21. Bots with the reaction feature receive special hooks when a reaction was added to a chat message.

Headers#
Header	Content type	Description
HTTP_X_NEXTCLOUD_TALK_SIGNATURE	[a-f0-9]{64}	SHA265 signature of the body
HTTP_X_NEXTCLOUD_TALK_RANDOM	[A-Za-z0-9+\]{64}	Random string used when signing the body
HTTP_X_NEXTCLOUD_TALK_BACKEND	URI	Base URL of the Nextcloud server sending the message
Content#
The content format follows the Activity Streams 2.0 Vocabulary.

Sample reaction removed message#
{
    "type": "Undo",
    "actor": {
        "type": "Person",
        "id": "users/ada-lovelace",
        "name": "Ada Lovelace"
    },
    "object": {
        "type": "Like",
        "actor": {
            "type": "Person",
            "id": "users/ada-lovelace",
            "name": "Ada Lovelace"
        },
        "object": {
            "type": "Note",
            "id": "1567",
            "name": "message",
            "content": "{\"message\":\"hi {mention-call1} !\",\"parameters\":{\"mention-call1\":{\"type\":\"call\",\"id\":\"n3xtc10ud\",\"name\":\"world\",\"call-type\":\"group\",\"icon-url\":\"https:\\/\\/nextcloud.local\\/ocs\\/v2.php\\/apps\\/spreed\\/api\\/v1\\/room\\/n3xtc10ud\\/avatar\"}}}",
            "mediaType": "text/markdown"
        },
        "target": {
            "type": "Collection",
            "id": "n3xtc10ud",
            "name": "world"
        },
        "content": "\ud83d\ude06"
    },
    "target": {
        "type": "Collection",
        "id": "n3xtc10ud",
        "name": "world"
    }
}
Explanation#
Path	Description
actor.id	One of the known attendee types followed by the / slash character and a unique identifier within the given type. For users it is the Nextcloud user ID, for guests and email invited guests a random hash value.
actor.name	The display name of the attendee sending the message.
actor.talkParticipantType	Optional: If applicable the attendee's participant type is provided. However this field can not be provided in some cases, e.g. bots.
object	Full hook content when the reaction was added
object.content	The reaction emoji that was removed
target.id	The token of the conversation in which the message was posted. It can be used to react or reply to the given message.
target.name	The name of the conversation in which the message was posted.
Bot added in a chat#
When the bot is added to a chat, the server sends a request to the bot, informing it of the event. The same signature/verification method is applied.

Headers#
Header	Content type	Description
HTTP_X_NEXTCLOUD_TALK_SIGNATURE	[a-f0-9]{64}	SHA265 signature of the body
HTTP_X_NEXTCLOUD_TALK_RANDOM	[A-Za-z0-9+\]{64}	Random string used when signing the body
HTTP_X_NEXTCLOUD_TALK_BACKEND	URI	Base URL of the Nextcloud server sending the message
Content#
The content format follows the Activity Streams 2.0 Vocabulary.

Sample request#
{
    "type": "Join",
    "actor": {
        "type": "Application",
        "id": "bots/bot-a78f46c5c203141b247554e180e1aa3553d282c6",
        "name": "Bot123"
    },
    "object": {
        "type": "Collection",
        "id": "n3xtc10ud",
        "name": "world"
    }
}
Explanation#
Path	Description
actor.id	Bot's actor type followed by the / slash character and a bot's unique sha1 identifier with bot- prefix.
actor.name	The display name of the bot.
object.id	The token of the conversation in which the bot was added.
object.name	The name of the conversation in which the bot was added.
Bot removed from a chat#
When the bot is removed from a chat, the server sends a request to the bot, informing it of the event. The same signature/verification method is applied.

Headers#
Header	Content type	Description
HTTP_X_NEXTCLOUD_TALK_SIGNATURE	[a-f0-9]{64}	SHA265 signature of the body
HTTP_X_NEXTCLOUD_TALK_RANDOM	[A-Za-z0-9+\]{64}	Random string used when signing the body
HTTP_X_NEXTCLOUD_TALK_BACKEND	URI	Base URL of the Nextcloud server sending the message
Content#
The content format follows the Activity Streams 2.0 Vocabulary.

Sample request#
{
    "type": "Leave",
    "actor": {
        "type": "Application",
        "id": "bots/bot-a78f46c5c203141b247554e180e1aa3553d282c6",
        "name": "Bot123"
    },
    "object": {
        "type": "Collection",
        "id": "n3xtc10ud",
        "name": "world"
    }
}
Explanation#
Path	Description
actor.id	Bot's actor type followed by the / slash character and a bot's unique sha1 identifier with bot- prefix.
actor.name	The display name of the bot.
object.id	The token of the conversation from which the bot was removed.
object.name	The name of the conversation from which the bot was removed.
Sending a chat message#
Bots can also send message. On the sending process the same signature/verification method is applied.

Required capability: bots-v1
Method: POST
Endpoint: /bot/{token}/message
Header:
Name	Description
X-Nextcloud-Talk-Bot-Random	The random value used when signing the request
X-Nextcloud-Talk-Bot-Signature	The signature to validate the request comes from an enabled bot
OCS-APIRequest	Needs to be set to true to access the ocs/vX.php endpoint
Data:
field	type	Description
message	string	The message the user wants to say
replyTo	int	The message ID this message is a reply to (only allowed for messages from the same conversation and when the message type is not system or command)
referenceId	string	A reference string to be able to identify the message again in a "get messages" request, should be a random sha256
silent	bool	If sent silent the message will not create chat notifications even for users
Response:
Status code:
201 Created When the message was posted successfully
400 Bad Request When the provided replyTo parameter is invalid or the message is empty
401 Unauthenticated When the bot could not be verified for the conversation
404 Not Found When the conversation could not be found
413 Payload Too Large When the message was longer than the allowed limit of 32000 characters (or 1000 until Nextcloud 16.0.1, check the spreed => config => chat => max-length capability for the limit)
429 Too Many Requests When 401 Unauthenticated was triggered too often
Sample bash script:#
#!/bin/bash

NC_URL="https://nextcloud.example.tld/" #  The URL of the Nextcloud instance (e.g., "https://nextcloud.example.com")
TOKEN="12345678" # The token of the conversation
SECRET="53CR3T" # Shared secret that is specified when installing a bot
MESSAGE=$1 # Pass the message as first argument

# Generate a random header and signature
RANDOM_HEADER=$(openssl rand -hex 32)
MESSAGE_TO_SIGN="${RANDOM_HEADER}${MESSAGE}"
SIGNATURE=$(echo -n "${MESSAGE_TO_SIGN}" | openssl dgst -sha256 -hmac "${SECRET}" | cut -d' ' -f2)

# Send the message
curl -X POST "${NC_URL}/ocs/v2.php/apps/spreed/api/v1/bot/${TOKEN}/message" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -H "OCS-APIRequest: true" \
  -H "X-Nextcloud-Talk-Bot-Random: ${RANDOM_HEADER}" \
  -H "X-Nextcloud-Talk-Bot-Signature: ${SIGNATURE}" \
  -d '{"message":"'"${MESSAGE}"'"}'
Reacting to a chat message#
Bots can also react to a message. The same signature/verification method is applied.

Required capability: bots-v1
Method: POST
Endpoint: /bot/{token}/reaction/{messageId}
Header:
Name	Description
X-Nextcloud-Talk-Bot-Random	The random value used when signing the request
X-Nextcloud-Talk-Bot-Signature	The signature to validate the request comes from an enabled bot
OCS-APIRequest	Needs to be set to true to access the ocs/vX.php endpoint
Data:
field	type	Description
reaction	string	A single emoji
Response:
Status code:
201 Created When the reaction was created successfully
400 Bad Request When the provided emoji was invalid
401 Unauthenticated When the bot could not be verified for the conversation
404 Not Found When the conversation or message could not be found
429 Too Many Requests When 401 Unauthenticated was triggered too often
Delete a reaction#
Bots can also remove their previous reaction from a message. The same signature/verification method is applied.

Required capability: bots-v1
Method: DELETE
Endpoint: /bot/{token}/reaction/{messageId}
Header:
Name	Description
X-Nextcloud-Talk-Bot-Random	The random value used when signing the request
X-Nextcloud-Talk-Bot-Signature	The signature to validate the request comes from an enabled bot
OCS-APIRequest	Needs to be set to true to access the ocs/vX.php endpoint
Data:
field	type	Description
reaction	string	A single emoji
Response:
Status code:
200 OK When the reaction was deleted successfully
400 Bad Request When the provided emoji was invalid
401 Unauthenticated When the bot could not be verified for the conversation
404 Not Found When the conversation or message could not be found
429 Too Many Requests When 401 Unauthenticated was triggered too often