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
    """Fast summary - just scans recent DMs and a few key channels."""
    import time
    cutoff = time.time() - (hours * 3600)
    lines = [f"# Quick Summary - {client.workspace.name}", ""]

    # Get conversations (this is cached-ish, fast)
    conversations = client.get_conversations()
    dm_convs = [c for c in conversations if c.type == "dm"]
    channel_convs = [c for c in conversations if c.type in ("channel", "group")]

    # Just check 5 most recent DMs
    dm_messages = []
    for conv in dm_convs[:5]:
        messages = client.get_messages(conv.id, limit=3, oldest=cutoff)
        # Resolve the DM name only for convs we're showing
        resolved_name = client.resolve_dm_name(conv)
        for msg in messages:
            msg.channel_name = resolved_name
            dm_messages.append(msg)

    dm_messages.sort(key=lambda m: m.ts, reverse=True)

    if dm_messages:
        lines.append("## Recent DMs")
        lines.append("")
        for msg in dm_messages[:10]:
            time_str = format_relative_time(msg.timestamp)
            text = truncate_text(msg.text, 60)
            lines.append(f"- **{msg.channel_name}** ({time_str}): \"{text}\"")
        lines.append("")

    # Check 5 most active channels for mentions
    mentions = []
    for conv in channel_convs[:5]:
        messages = client.get_messages(conv.id, limit=20, oldest=cutoff)
        for msg in messages:
            if msg.is_mention:
                msg.channel_name = conv.name
                mentions.append(msg)

    if mentions:
        lines.append("## Mentions")
        lines.append("")
        for msg in mentions[:10]:
            from_name = msg.user_name or "Someone"
            time_str = format_relative_time(msg.timestamp)
            text = truncate_text(msg.text, 60)
            lines.append(f"- **{msg.channel_name}** - {from_name} ({time_str}): \"{text}\"")
        lines.append("")

    # Quick channel activity counts
    lines.append("## Channel Activity (top 10)")
    lines.append("")
    lines.append(f"You're in {len(channel_convs)} channels, {len(dm_convs)} DMs")
    lines.append("")

    return "\n".join(lines)


def summarize_workspace(client: SlackClient, hours: int = 24, max_channels: int = 10) -> WorkspaceSummary:
    """Generate a summary for a single workspace.

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

    # Process DMs (limit to 10 most recent, fewer messages)
    for conv in dm_convs[:10]:
        messages = client.get_messages(conv.id, limit=5, oldest=cutoff)
        for msg in messages:
            msg.channel_name = conv.name
        if messages:
            summary.dm_count += len(messages)
            summary.dms.extend(messages)

    # Sort DMs by time
    summary.dms.sort(key=lambda m: m.ts, reverse=True)
    summary.dms = summary.dms[:20]  # Keep top 20

    # Skip full mention scan - too expensive. Will catch mentions in channel scan.
    summary.mentions = []
    summary.mention_count = 0

    # Process channels (limit to avoid rate limits)
    for conv in channel_convs[:max_channels]:
        messages = client.get_messages(conv.id, limit=50)

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

        # Create channel summary
        channel_summary = ChannelSummary(
            name=conv.name,
            message_count=len(messages),
            has_mentions=has_mentions,
            has_action_items=len(action_items) > 0,
            preview=truncate_text(messages[0].text) if messages else "",
            top_messages=messages[:3],
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
    """Format workspace summaries as markdown."""
    now = datetime.now(timezone.utc)
    lines = [
        f"# Slack Summary - {now.strftime('%B %d, %Y')}",
        "",
    ]

    for ws in summaries:
        if len(summaries) > 1:
            lines.append(f"## {ws.name}")
            lines.append("")

        # Action Items
        if ws.action_items:
            lines.append("## Needs Your Attention")
            lines.append("")
            lines.append("| From | Channel | Message | Time |")
            lines.append("|------|---------|---------|------|")
            for msg in ws.action_items[:10]:
                from_name = msg.user_name or "Unknown"
                text = truncate_text(msg.text, 60)
                time_str = format_relative_time(msg.timestamp)
                lines.append(f"| {from_name} | {msg.channel_name} | {text} | {time_str} |")
            lines.append("")

        # Direct Messages
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

            for sender, messages in list(by_sender.items())[:10]:
                if len(messages) == 1:
                    text = truncate_text(messages[0].text, 80)
                    time_str = format_relative_time(messages[0].timestamp)
                    lines.append(f"- **{sender}** ({time_str}): \"{text}\"")
                else:
                    lines.append(f"- **{sender}**: {len(messages)} messages")
                    for msg in messages[:3]:
                        text = truncate_text(msg.text, 60)
                        time_str = format_relative_time(msg.timestamp)
                        lines.append(f"  - ({time_str}) \"{text}\"")

            lines.append("")

        # Mentions
        if ws.mentions:
            lines.append("## Mentions")
            lines.append("")
            for msg in ws.mentions[:10]:
                from_name = msg.user_name or "Unknown"
                text = truncate_text(msg.text, 60)
                time_str = format_relative_time(msg.timestamp)
                lines.append(f"- **{msg.channel_name}** - {from_name} ({time_str}): \"{text}\"")
            lines.append("")

        # Channel Activity
        if ws.channels:
            lines.append("## Channel Activity")
            lines.append("")

            # Split into high and low activity
            high_activity = [c for c in ws.channels if c.message_count >= 10]
            low_activity = [c for c in ws.channels if 0 < c.message_count < 10]

            if high_activity:
                lines.append("### High Activity")
                lines.append("")
                for ch in high_activity[:10]:
                    flags = []
                    if ch.has_mentions:
                        flags.append("mentions you")
                    if ch.has_action_items:
                        flags.append("needs response")
                    flag_str = f" - *{', '.join(flags)}*" if flags else ""
                    lines.append(f"- **{ch.name}** ({ch.message_count} messages){flag_str}")
                    if ch.preview:
                        lines.append(f"  - Latest: \"{ch.preview}\"")
                lines.append("")

            if low_activity:
                lines.append("### Low Activity")
                lines.append("")
                for ch in low_activity[:10]:
                    lines.append(f"- **{ch.name}** ({ch.message_count} messages)")
                lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)
