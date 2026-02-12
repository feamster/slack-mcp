"""Slack API client wrapper for reading and writing messages."""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from .config import WorkspaceConfig

# Rate limiting - Slack tier 3 allows 50+ requests per minute
# Only add delay when paginating, not for single requests
RATE_LIMIT_DELAY = 0.05  # seconds between paginated API calls


@dataclass
class User:
    """Slack user info."""
    id: str
    name: str
    real_name: str
    is_bot: bool = False


@dataclass
class Message:
    """A Slack message."""
    ts: str
    text: str
    user_id: Optional[str]
    user_name: Optional[str] = None
    channel_id: str = ""
    channel_name: str = ""
    thread_ts: Optional[str] = None
    reply_count: int = 0
    is_mention: bool = False
    timestamp: Optional[datetime] = None

    @property
    def permalink(self) -> str:
        """Generate a message link (requires workspace URL)."""
        return f"slack://channel?id={self.channel_id}&message={self.ts}"


@dataclass
class Conversation:
    """A Slack conversation (channel, DM, or group DM)."""
    id: str
    name: str
    type: str  # 'channel', 'dm', 'mpim', 'group'
    is_member: bool = True
    is_archived: bool = False
    unread_count: int = 0
    last_read: Optional[str] = None
    latest_message: Optional[Message] = None


@dataclass
class SlackClient:
    """Client for interacting with a single Slack workspace."""
    workspace: WorkspaceConfig
    client: WebClient = field(init=False)
    _user_cache: dict[str, User] = field(default_factory=dict)
    _my_user_id: Optional[str] = None
    _conversations_cache: Optional[list[Conversation]] = None
    _conversations_cache_time: float = 0

    def __post_init__(self):
        self.client = WebClient(token=self.workspace.token)

    @property
    def my_user_id(self) -> str:
        """Get the authenticated user's ID."""
        if self._my_user_id is None:
            response = self.client.auth_test()
            self._my_user_id = response["user_id"]
        return self._my_user_id

    def get_user(self, user_id: str) -> User:
        """Get user info, with caching."""
        if user_id not in self._user_cache:
            try:
                response = self.client.users_info(user=user_id)
                user_data = response["user"]
                self._user_cache[user_id] = User(
                    id=user_id,
                    name=user_data.get("name", "unknown"),
                    real_name=user_data.get("real_name", user_data.get("name", "Unknown")),
                    is_bot=user_data.get("is_bot", False),
                )
            except SlackApiError:
                self._user_cache[user_id] = User(
                    id=user_id,
                    name="unknown",
                    real_name="Unknown User",
                )
        return self._user_cache[user_id]

    def get_conversations(self, types: str = "public_channel,private_channel,mpim,im", use_cache: bool = True) -> list[Conversation]:
        """Get all conversations the user is a member of."""
        # Return cached result if fresh (5 min cache)
        cache_ttl = 300
        if use_cache and self._conversations_cache and (time.time() - self._conversations_cache_time) < cache_ttl:
            # Filter cached results by type
            type_set = set(types.split(","))
            type_map = {"public_channel": "channel", "private_channel": "group", "mpim": "mpim", "im": "dm"}
            allowed = {type_map.get(t, t) for t in type_set}
            return [c for c in self._conversations_cache if c.type in allowed]

        conversations = []
        cursor = None

        while True:
            time.sleep(RATE_LIMIT_DELAY)
            response = self.client.conversations_list(
                types=types,
                exclude_archived=True,
                limit=200,
                cursor=cursor,
            )

            for conv in response["channels"]:
                conv_type = self._get_conversation_type(conv)
                name = self._get_conversation_name(conv, conv_type)

                conversations.append(Conversation(
                    id=conv["id"],
                    name=name,
                    type=conv_type,
                    is_member=conv.get("is_member", True),
                    is_archived=conv.get("is_archived", False),
                    unread_count=conv.get("unread_count", 0),
                    last_read=conv.get("last_read"),
                ))

            cursor = response.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

        # Cache all conversations (we fetched all types)
        if types == "public_channel,private_channel,mpim,im":
            self._conversations_cache = conversations
            self._conversations_cache_time = time.time()

        return conversations

    def _get_conversation_type(self, conv: dict) -> str:
        """Determine conversation type."""
        if conv.get("is_im"):
            return "dm"
        if conv.get("is_mpim"):
            return "mpim"
        if conv.get("is_private"):
            return "group"
        return "channel"

    def _get_conversation_name(self, conv: dict, conv_type: str, resolve_users: bool = False) -> str:
        """Get a human-readable name for the conversation."""
        if conv_type == "dm":
            user_id = conv.get("user")
            if user_id:
                if resolve_users:
                    user = self.get_user(user_id)
                    return f"@{user.real_name}"
                else:
                    # Defer user resolution - just use ID for now
                    return f"@user:{user_id}"
            return "@Unknown"
        if conv_type == "mpim":
            return conv.get("name", "Group DM").replace("mpdm-", "").replace("--", ", ")
        return f"#{conv.get('name', 'unknown')}"

    def resolve_dm_name(self, conv: Conversation) -> str:
        """Resolve a DM conversation name to the real user name."""
        if conv.name.startswith("@user:"):
            user_id = conv.name[6:]
            user = self.get_user(user_id)
            return f"@{user.real_name}"
        return conv.name

    def get_messages(
        self,
        channel_id: str,
        limit: int = 100,
        oldest: Optional[float] = None,
        latest: Optional[float] = None,
    ) -> list[Message]:
        """Get messages from a channel."""
        kwargs = {"channel": channel_id, "limit": limit}
        if oldest:
            kwargs["oldest"] = str(oldest)
        if latest:
            kwargs["latest"] = str(latest)

        try:
            response = self.client.conversations_history(**kwargs)
        except SlackApiError as e:
            if e.response["error"] == "channel_not_found":
                return []
            raise

        messages = []
        for msg in response.get("messages", []):
            if msg.get("subtype") in ("channel_join", "channel_leave", "bot_message"):
                continue

            user_id = msg.get("user")
            user_name = None
            if user_id:
                user = self.get_user(user_id)
                user_name = user.real_name

            # Check if this message mentions the current user
            is_mention = f"<@{self.my_user_id}>" in msg.get("text", "")

            ts = msg.get("ts", "")
            messages.append(Message(
                ts=ts,
                text=msg.get("text", ""),
                user_id=user_id,
                user_name=user_name,
                channel_id=channel_id,
                thread_ts=msg.get("thread_ts"),
                reply_count=msg.get("reply_count", 0),
                is_mention=is_mention,
                timestamp=datetime.fromtimestamp(float(ts), tz=timezone.utc) if ts else None,
            ))

        return messages

    def get_thread(self, channel_id: str, thread_ts: str, limit: int = 100) -> list[Message]:
        """Get messages in a thread."""
        try:
            response = self.client.conversations_replies(
                channel=channel_id,
                ts=thread_ts,
                limit=limit,
            )
        except SlackApiError:
            return []

        messages = []
        for msg in response.get("messages", []):
            user_id = msg.get("user")
            user_name = None
            if user_id:
                user = self.get_user(user_id)
                user_name = user.real_name

            ts = msg.get("ts", "")
            messages.append(Message(
                ts=ts,
                text=msg.get("text", ""),
                user_id=user_id,
                user_name=user_name,
                channel_id=channel_id,
                thread_ts=thread_ts,
                timestamp=datetime.fromtimestamp(float(ts), tz=timezone.utc) if ts else None,
            ))

        return messages

    def get_unread_messages(self, hours: int = 24) -> dict[str, list[Message]]:
        """Get unread messages from all conversations."""
        cutoff = time.time() - (hours * 3600)
        conversations = self.get_conversations()
        unread: dict[str, list[Message]] = {}

        for conv in conversations:
            if conv.is_archived:
                continue

            # Get messages since last_read or cutoff
            oldest = float(conv.last_read) if conv.last_read else cutoff

            messages = self.get_messages(conv.id, limit=50, oldest=oldest)
            if messages:
                # Add channel name to messages
                for msg in messages:
                    msg.channel_name = conv.name
                unread[conv.name] = messages

        return unread

    def get_mentions(self, hours: int = 24) -> list[Message]:
        """Get messages where the user was mentioned."""
        cutoff = time.time() - (hours * 3600)
        conversations = self.get_conversations()
        mentions = []

        for conv in conversations:
            if conv.is_archived:
                continue

            messages = self.get_messages(conv.id, limit=100, oldest=cutoff)
            for msg in messages:
                if msg.is_mention:
                    msg.channel_name = conv.name
                    mentions.append(msg)

        # Sort by timestamp, newest first
        mentions.sort(key=lambda m: m.ts, reverse=True)
        return mentions

    def search_messages(
        self,
        query: str,
        count: int = 20,
        sort: str = "timestamp",
    ) -> list[Message]:
        """Search messages across the workspace."""
        try:
            response = self.client.search_messages(
                query=query,
                count=count,
                sort=sort,
            )
        except SlackApiError:
            return []

        messages = []
        for match in response.get("messages", {}).get("matches", []):
            user_id = match.get("user")
            user_name = match.get("username")

            channel = match.get("channel", {})
            channel_id = channel.get("id", "")
            channel_name = channel.get("name", "unknown")

            ts = match.get("ts", "")
            messages.append(Message(
                ts=ts,
                text=match.get("text", ""),
                user_id=user_id,
                user_name=user_name,
                channel_id=channel_id,
                channel_name=f"#{channel_name}",
                timestamp=datetime.fromtimestamp(float(ts), tz=timezone.utc) if ts else None,
            ))

        return messages

    # Write methods

    def send_message(
        self,
        channel: str,
        text: str,
        thread_ts: Optional[str] = None,
        context_for_ts: Optional[str] = None,
    ) -> Message:
        """Send a message to a channel or DM.

        Args:
            channel: Channel name or ID
            text: Message text
            thread_ts: Thread to reply in (optional)
            context_for_ts: If replying to a specific message, provide its ts
                           to auto-add context if it's not the most recent
        """
        # Resolve channel name to ID if needed
        channel_id = self._resolve_channel(channel)

        # If replying to a specific message, check if we need to add context
        if context_for_ts:
            text = self._maybe_add_reply_context(channel_id, context_for_ts, text)

        kwargs = {"channel": channel_id, "text": text}
        if thread_ts:
            kwargs["thread_ts"] = thread_ts

        response = self.client.chat_postMessage(**kwargs)

        return Message(
            ts=response["ts"],
            text=text,
            user_id=self.my_user_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
        )

    def _maybe_add_reply_context(self, channel_id: str, reply_to_ts: str, text: str) -> str:
        """Add context prefix if replying to a non-recent message."""
        # Get recent messages to check if this is the most recent
        recent = self.get_messages(channel_id, limit=3)

        if not recent:
            return text

        # If the message we're replying to is the most recent, no context needed
        if recent[0].ts == reply_to_ts:
            return text

        # Find the original message to get context
        original = None
        for msg in recent:
            if msg.ts == reply_to_ts:
                original = msg
                break

        # If not in recent, fetch it specifically
        if not original:
            try:
                response = self.client.conversations_history(
                    channel=channel_id,
                    latest=reply_to_ts,
                    limit=1,
                    inclusive=True,
                )
                if response.get("messages"):
                    msg_data = response["messages"][0]
                    original = Message(
                        ts=msg_data["ts"],
                        text=msg_data.get("text", ""),
                        user_id=msg_data.get("user"),
                        channel_id=channel_id,
                    )
            except:
                pass

        if original and original.text:
            # Extract brief context (first ~50 chars, cut at word boundary)
            context = original.text[:50]
            if len(original.text) > 50:
                context = context.rsplit(' ', 1)[0] + "..."
            # Clean up formatting
            context = context.replace('\n', ' ').strip()
            return f'Re: "{context}" — {text}'

        return text

    def reply_to_thread(
        self,
        channel: str,
        thread_ts: str,
        text: str,
    ) -> Message:
        """Reply to a thread."""
        return self.send_message(channel, text, thread_ts=thread_ts)

    def add_reaction(self, channel: str, timestamp: str, emoji: str) -> bool:
        """Add an emoji reaction to a message."""
        channel_id = self._resolve_channel(channel)
        # Remove colons if present
        emoji = emoji.strip(":")

        try:
            self.client.reactions_add(
                channel=channel_id,
                timestamp=timestamp,
                name=emoji,
            )
            return True
        except SlackApiError:
            return False

    def _resolve_channel(self, channel: str) -> str:
        """Resolve a channel name or @user to a channel ID."""
        # Already an ID
        if channel.startswith("C") or channel.startswith("D") or channel.startswith("G"):
            return channel

        # Remove # prefix
        if channel.startswith("#"):
            channel = channel[1:]

        # Handle @user for DMs
        if channel.startswith("@"):
            username = channel[1:]
            # Find user by name
            for conv in self.get_conversations(types="im"):
                if username.lower() in conv.name.lower():
                    return conv.id
            raise ValueError(f"Could not find DM with user: {username}")

        # Find channel by name
        for conv in self.get_conversations(types="public_channel,private_channel"):
            if conv.name.lstrip("#") == channel:
                return conv.id

        raise ValueError(f"Could not find channel: {channel}")

    def find_dm_by_person(self, person: str) -> tuple[str, str]:
        """Find a DM conversation by person name.

        Args:
            person: Person's name (partial match, case insensitive)

        Returns:
            Tuple of (channel_id, resolved_name)
        """
        person_lower = person.lower().strip().lstrip("@")
        dm_convs = self.get_conversations(types="im")

        # Try exact match first, then partial
        for conv in dm_convs:
            # Resolve the name for matching
            resolved = self.resolve_dm_name(conv)
            name_lower = resolved.lower().lstrip("@")

            if person_lower == name_lower:
                return conv.id, resolved

        # Partial match
        for conv in dm_convs:
            resolved = self.resolve_dm_name(conv)
            name_lower = resolved.lower().lstrip("@")

            if person_lower in name_lower:
                return conv.id, resolved

        raise ValueError(f"Could not find DM with: {person}")
