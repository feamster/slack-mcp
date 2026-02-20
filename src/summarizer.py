"""Message summarization and categorization."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .slack_client import Message, SlackClient


@dataclass
class ChannelSummary:
    """Summary of activity in a channel."""
    name: str
    message_count: int
    unread_count: int = 0
    has_mentions: bool = False
    has_action_items: bool = False
    preview: str = ""
    top_messages: list[Message] = field(default_factory=list)


@dataclass
class WorkspaceSummary:
    """Summary of a single workspace."""
    name: str
    dm_count: int = 0
    mention_count: int = 0
    channel_message_count: int = 0
    action_items: list[Message] = field(default_factory=list)
    dms: list[Message] = field(default_factory=list)
    mentions: list[Message] = field(default_factory=list)
    channels: list[ChannelSummary] = field(default_factory=list)


def is_action_item(message: Message, my_user_id: str) -> bool:
    """Detect if a message is an action item (question or request)."""
    text = message.text.lower()

    # Check if it's directed at the user
    if f"<@{my_user_id}>" not in message.text:
        return False

    # Question patterns
    question_patterns = [
        r"\?$",  # Ends with question mark
        r"^(can|could|would|will|do|does|did|is|are|have|has|should)\s",
        r"(please|pls)\s",
        r"(need|needs)\s+(you|your)",
        r"(review|check|look at|take a look)",
        r"(thoughts|opinion|input|feedback)\?",
        r"when (can|will|could)",
        r"eta\??",
    ]

    for pattern in question_patterns:
        if re.search(pattern, text):
            return True

    return False


def format_relative_time(dt: Optional[datetime]) -> str:
    """Format a datetime as relative time."""
    if not dt:
        return "unknown"

    now = datetime.now(timezone.utc)
    diff = now - dt

    seconds = diff.total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        minutes = int(seconds / 60)
        return f"{minutes}m ago"
    if seconds < 86400:
        hours = int(seconds / 3600)
        return f"{hours}h ago"
    days = int(seconds / 86400)
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days}d ago"
    return dt.strftime("%b %d")


def truncate_text(text: str, max_len: int = 100) -> str:
    """Truncate text with ellipsis."""
    # Remove user mentions formatting
    text = re.sub(r"<@\w+>", "@user", text)
    # Remove channel mentions formatting
    text = re.sub(r"<#\w+\|([^>]+)>", r"#\1", text)
    # Remove link formatting
    text = re.sub(r"<([^|>]+)\|([^>]+)>", r"\2", text)
    text = re.sub(r"<([^>]+)>", r"\1", text)
    # Collapse whitespace
    text = " ".join(text.split())

    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def quick_summary(client: SlackClient, hours: int = 24) -> str:
    """Fast summary - scans recent DMs and key channels with FULL message text."""
    import time
    cutoff = time.time() - (hours * 3600)
    lines = [f"# Quick Summary - {client.workspace.name}", ""]

    # Get conversations (this is cached-ish, fast)
    conversations = client.get_conversations()
    dm_convs = [c for c in conversations if c.type == "dm"]
    channel_convs = [c for c in conversations if c.type in ("channel", "group")]

    # Check 8 most recent DMs, get 3 messages each (full text)
    dm_by_person: dict[str, list[Message]] = {}
    for conv in dm_convs[:8]:
        messages = client.get_messages(conv.id, limit=3, oldest=cutoff)
        if messages:
            resolved_name = client.resolve_dm_name(conv)
            for msg in messages:
                msg.channel_name = resolved_name
            dm_by_person[resolved_name] = messages

    if dm_by_person:
        lines.append("## Recent DMs")
        lines.append("")
        for person, messages in dm_by_person.items():
            lines.append(f"### {person}")
            for msg in messages:
                time_str = format_relative_time(msg.timestamp)
                sender = "You" if msg.user_id == client.my_user_id else person.lstrip("@")
                text = msg.text.replace("\n", " ")  # Full text, no truncation
                thread_marker = f" [thread: {msg.reply_count} replies]" if msg.reply_count else ""
                lines.append(f"- **{sender}** ({time_str}){thread_marker}: {text}")
            lines.append("")

    # Check 8 most active channels for mentions AND recent messages
    channel_activity: dict[str, list[Message]] = {}
    mentions = []
    for conv in channel_convs[:8]:
        messages = client.get_messages(conv.id, limit=5, oldest=cutoff)
        if messages:
            for msg in messages:
                msg.channel_name = conv.name
                if msg.is_mention:
                    mentions.append(msg)
            channel_activity[conv.name] = messages

    if mentions:
        lines.append("## Mentions")
        lines.append("")
        for msg in mentions[:10]:
            from_name = msg.user_name or "Someone"
            time_str = format_relative_time(msg.timestamp)
            text = msg.text.replace("\n", " ")  # Full text
            thread_marker = f" [thread: {msg.reply_count} replies]" if msg.reply_count else ""
            lines.append(f"- **{msg.channel_name}** - {from_name} ({time_str}){thread_marker}: {text}")
        lines.append("")

    # Show actual channel messages (not just counts)
    if channel_activity:
        lines.append("## Channel Activity")
        lines.append("")
        for channel_name, messages in channel_activity.items():
            thread_count = sum(1 for m in messages if m.reply_count and m.reply_count > 0)
            thread_info = f" ({thread_count} with threads)" if thread_count else ""
            lines.append(f"### {channel_name} ({len(messages)} messages{thread_info})")
            for msg in messages[:3]:  # Show top 3 messages per channel
                from_name = msg.user_name or "Unknown"
                time_str = format_relative_time(msg.timestamp)
                text = msg.text.replace("\n", " ")  # Full text
                thread_marker = f" [thread: {msg.reply_count} replies]" if msg.reply_count else ""
                lines.append(f"- **{from_name}** ({time_str}){thread_marker}: {text}")
            if len(messages) > 3:
                lines.append(f"- _...and {len(messages) - 3} more messages_")
            lines.append("")

    lines.append(f"_Scanned {len(dm_by_person)} DMs and {len(channel_activity)} channels. You're in {len(channel_convs)} channels total._")
    lines.append("")

    return "\n".join(lines)


def summarize_workspace(client: SlackClient, hours: int = 24, max_channels: int = 15) -> WorkspaceSummary:
    """Generate a detailed summary for a single workspace with FULL message text.

    Args:
        client: Slack client
        hours: Hours to look back
        max_channels: Max channels to scan (to avoid rate limits)
    """
    import time
    cutoff = time.time() - (hours * 3600)

    summary = WorkspaceSummary(name=client.workspace.name)

    # Get all conversations
    conversations = client.get_conversations()

    # Categorize conversations
    dm_convs = [c for c in conversations if c.type == "dm"]
    channel_convs = [c for c in conversations if c.type in ("channel", "group")]

    # Process DMs - more conversations, more messages
    for conv in dm_convs[:15]:
        messages = client.get_messages(conv.id, limit=10, oldest=cutoff)
        # Resolve DM name to actual person
        resolved_name = client.resolve_dm_name(conv)
        for msg in messages:
            msg.channel_name = resolved_name
        if messages:
            summary.dm_count += len(messages)
            summary.dms.extend(messages)

    # Sort DMs by time
    summary.dms.sort(key=lambda m: m.ts, reverse=True)
    summary.dms = summary.dms[:50]  # Keep top 50

    # Skip full mention scan - too expensive. Will catch mentions in channel scan.
    summary.mentions = []
    summary.mention_count = 0

    # Process channels with more messages each
    for conv in channel_convs[:max_channels]:
        messages = client.get_messages(conv.id, limit=30, oldest=cutoff)

        if not messages:
            continue

        # Check for mentions and action items
        has_mentions = any(m.is_mention for m in messages)
        action_items = [m for m in messages if is_action_item(m, client.my_user_id)]

        for msg in messages:
            msg.channel_name = conv.name
            if msg.is_mention:
                summary.mentions.append(msg)

        if action_items:
            summary.action_items.extend(action_items)

        # Create channel summary - store more messages for full display
        channel_summary = ChannelSummary(
            name=conv.name,
            message_count=len(messages),
            has_mentions=has_mentions,
            has_action_items=len(action_items) > 0,
            preview=messages[0].text.replace("\n", " ") if messages else "",  # Full text for preview
            top_messages=messages[:10],  # Keep more messages for full mode
        )
        summary.channels.append(channel_summary)
        summary.channel_message_count += len(messages)

    # Sort channels by activity
    summary.channels.sort(key=lambda c: c.message_count, reverse=True)

    # Sort action items and mentions by time
    summary.action_items.sort(key=lambda m: m.ts, reverse=True)
    summary.mentions.sort(key=lambda m: m.ts, reverse=True)
    summary.mention_count = len(summary.mentions)

    return summary


def format_summary_markdown(summaries: list[WorkspaceSummary]) -> str:
    """Format workspace summaries as markdown with FULL message text."""
    now = datetime.now(timezone.utc)
    lines = [
        f"# Slack Summary - {now.strftime('%B %d, %Y')}",
        "",
    ]

    for ws in summaries:
        if len(summaries) > 1:
            lines.append(f"## {ws.name}")
            lines.append("")

        # Action Items - FULL text
        if ws.action_items:
            lines.append("## Needs Your Attention")
            lines.append("")
            for msg in ws.action_items[:10]:
                from_name = msg.user_name or "Unknown"
                text = msg.text.replace("\n", " ")  # Full text
                time_str = format_relative_time(msg.timestamp)
                thread_marker = f" [thread: {msg.reply_count} replies]" if msg.reply_count else ""
                lines.append(f"### {msg.channel_name} - {from_name} ({time_str}){thread_marker}")
                lines.append(f"{text}")
                lines.append(f"_ts: {msg.ts}_")
                lines.append("")

        # Direct Messages - FULL text
        if ws.dms:
            lines.append("## Direct Messages")
            lines.append("")

            # Group by sender
            by_sender: dict[str, list[Message]] = {}
            for msg in ws.dms:
                sender = msg.channel_name
                if sender not in by_sender:
                    by_sender[sender] = []
                by_sender[sender].append(msg)

            for sender, messages in list(by_sender.items())[:15]:
                thread_count = sum(1 for m in messages if m.reply_count and m.reply_count > 0)
                thread_info = f" ({thread_count} with threads)" if thread_count else ""
                lines.append(f"### {sender} ({len(messages)} messages{thread_info})")
                for msg in messages[:5]:  # Up to 5 messages per DM
                    text = msg.text.replace("\n", " ")  # Full text
                    time_str = format_relative_time(msg.timestamp)
                    thread_marker = f" [thread: {msg.reply_count} replies]" if msg.reply_count else ""
                    lines.append(f"- ({time_str}){thread_marker}: {text}")
                if len(messages) > 5:
                    lines.append(f"- _...and {len(messages) - 5} more messages_")
                lines.append("")

        # Mentions - FULL text
        if ws.mentions:
            lines.append("## Mentions")
            lines.append("")
            for msg in ws.mentions[:15]:
                from_name = msg.user_name or "Unknown"
                text = msg.text.replace("\n", " ")  # Full text
                time_str = format_relative_time(msg.timestamp)
                thread_marker = f" [thread: {msg.reply_count} replies]" if msg.reply_count else ""
                lines.append(f"- **{msg.channel_name}** - {from_name} ({time_str}){thread_marker}:")
                lines.append(f"  {text}")
                lines.append(f"  _ts: {msg.ts}_")
                lines.append("")

        # Channel Activity - show actual messages, not just counts
        if ws.channels:
            lines.append("## Channel Activity")
            lines.append("")

            for ch in ws.channels[:15]:
                flags = []
                if ch.has_mentions:
                    flags.append("mentions you")
                if ch.has_action_items:
                    flags.append("needs response")
                flag_str = f" *[{', '.join(flags)}]*" if flags else ""
                thread_count = sum(1 for m in ch.top_messages if m.reply_count and m.reply_count > 0)
                thread_info = f", {thread_count} with threads" if thread_count else ""
                lines.append(f"### {ch.name} ({ch.message_count} messages{thread_info}){flag_str}")

                # Show actual messages, not just preview
                for msg in ch.top_messages[:5]:
                    from_name = msg.user_name or "Unknown"
                    time_str = format_relative_time(msg.timestamp)
                    text = msg.text.replace("\n", " ")  # Full text
                    thread_marker = f" [thread: {msg.reply_count} replies]" if msg.reply_count else ""
                    lines.append(f"- **{from_name}** ({time_str}){thread_marker}: {text}")
                if ch.message_count > 5:
                    lines.append(f"- _...and {ch.message_count - 5} more messages_")
                lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)
