"""MCP server for Slack integration."""

import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    TextContent,
    Tool,
)

from .config import get_config
from .slack_client import SlackClient
from .summarizer import format_summary_markdown, summarize_workspace, quick_summary, truncate_text, format_relative_time

# Initialize MCP server
server = Server("slack-mcp")

# Lazy-loaded clients per workspace
_clients: dict[str, SlackClient] = {}


def get_client(workspace: str | None = None) -> SlackClient:
    """Get or create a Slack client for a workspace."""
    config = get_config()
    ws = config.get_workspace(workspace)

    if ws.key not in _clients:
        _clients[ws.key] = SlackClient(workspace=ws)

    return _clients[ws.key]


def get_all_clients() -> list[SlackClient]:
    """Get clients for all configured workspaces."""
    config = get_config()
    clients = []
    for ws in config.list_workspaces():
        if ws.key not in _clients:
            _clients[ws.key] = SlackClient(workspace=ws)
        clients.append(_clients[ws.key])
    return clients


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available Slack tools."""
    return [
        # Read tools
        Tool(
            name="slack_workspaces",
            description="List all configured Slack workspaces",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="slack_summary",
            description="Get a summary of Slack activity (DMs, mentions, channels). Use 'quick' mode for fast overview, 'full' for detailed scan.",
            inputSchema={
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "description": "'quick' (fast, just recent activity) or 'full' (slower, detailed scan). Default: quick",
                        "default": "quick",
                    },
                    "hours": {
                        "type": "number",
                        "description": "Number of hours to look back (default: 24, max recommended: 168 for week)",
                        "default": 24,
                    },
                    "workspace": {
                        "type": "string",
                        "description": "Specific workspace to summarize (optional, defaults to all)",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="slack_unread",
            description="Get unread messages from DMs and key channels",
            inputSchema={
                "type": "object",
                "properties": {
                    "hours": {
                        "type": "number",
                        "description": "Hours to look back (default: 24)",
                        "default": 24,
                    },
                    "max_dms": {
                        "type": "number",
                        "description": "Max DM conversations to check (default: 15)",
                        "default": 15,
                    },
                    "max_channels": {
                        "type": "number",
                        "description": "Max channels to check (default: 15)",
                        "default": 15,
                    },
                    "workspace": {
                        "type": "string",
                        "description": "Specific workspace (optional)",
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="slack_channel",
            description="Read recent messages from a specific channel",
            inputSchema={
                "type": "object",
                "properties": {
                    "channel": {
                        "type": "string",
                        "description": "Channel name (e.g., #general) or ID",
                    },
                    "limit": {
                        "type": "number",
                        "description": "Number of messages to fetch (default: 20)",
                        "default": 20,
                    },
                    "workspace": {
                        "type": "string",
                        "description": "Workspace key (optional)",
                    },
                },
                "required": ["channel"],
            },
        ),
        Tool(
            name="slack_dm",
            description="Read recent messages from a DM conversation with a specific person",
            inputSchema={
                "type": "object",
                "properties": {
                    "person": {
                        "type": "string",
                        "description": "Person's name (e.g., 'Jen Rexford', 'jen', '@jennifer')",
                    },
                    "limit": {
                        "type": "number",
                        "description": "Number of messages to fetch (default: 20)",
                        "default": 20,
                    },
                    "workspace": {
                        "type": "string",
                        "description": "Workspace key (optional)",
                    },
                },
                "required": ["person"],
            },
        ),
        Tool(
            name="slack_thread",
            description="Read messages in a specific thread",
            inputSchema={
                "type": "object",
                "properties": {
                    "channel": {
                        "type": "string",
                        "description": "Channel name or ID",
                    },
                    "thread_ts": {
                        "type": "string",
                        "description": "Thread timestamp (the ts of the parent message)",
                    },
                    "workspace": {
                        "type": "string",
                        "description": "Workspace key (optional)",
                    },
                },
                "required": ["channel", "thread_ts"],
            },
        ),
        Tool(
            name="slack_search",
            description="Search for messages across Slack",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (supports Slack search syntax)",
                    },
                    "count": {
                        "type": "number",
                        "description": "Number of results (default: 20)",
                        "default": 20,
                    },
                    "workspace": {
                        "type": "string",
                        "description": "Workspace key (optional)",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="slack_channels",
            description="List all channels you're a member of",
            inputSchema={
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "description": "Filter by type: 'all', 'channels', 'dms', 'groups' (default: 'channels')",
                        "default": "channels",
                    },
                    "workspace": {
                        "type": "string",
                        "description": "Workspace key (optional)",
                    },
                },
                "required": [],
            },
        ),
        # Write tools
        Tool(
            name="slack_send",
            description="Send a message to a channel or DM. If replying to a specific message (not the most recent), provide reply_to_ts to auto-add context.",
            inputSchema={
                "type": "object",
                "properties": {
                    "channel": {
                        "type": "string",
                        "description": "Channel name (#channel), user (@username), or ID",
                    },
                    "text": {
                        "type": "string",
                        "description": "Message text to send",
                    },
                    "reply_to_ts": {
                        "type": "string",
                        "description": "Timestamp of message being replied to (adds context if not most recent)",
                    },
                    "workspace": {
                        "type": "string",
                        "description": "Workspace key (optional)",
                    },
                },
                "required": ["channel", "text"],
            },
        ),
        Tool(
            name="slack_reply",
            description="Reply to a message in a thread. If replying to a specific message in the thread (not the most recent), provide reply_to_ts to auto-add context.",
            inputSchema={
                "type": "object",
                "properties": {
                    "channel": {
                        "type": "string",
                        "description": "Channel name or ID",
                    },
                    "thread_ts": {
                        "type": "string",
                        "description": "Timestamp of the parent message (thread root)",
                    },
                    "text": {
                        "type": "string",
                        "description": "Reply text",
                    },
                    "reply_to_ts": {
                        "type": "string",
                        "description": "Timestamp of specific message being replied to (adds context if not most recent in thread)",
                    },
                    "workspace": {
                        "type": "string",
                        "description": "Workspace key (optional)",
                    },
                },
                "required": ["channel", "thread_ts", "text"],
            },
        ),
        Tool(
            name="slack_react",
            description="Add an emoji reaction to a message",
            inputSchema={
                "type": "object",
                "properties": {
                    "channel": {
                        "type": "string",
                        "description": "Channel name or ID",
                    },
                    "timestamp": {
                        "type": "string",
                        "description": "Message timestamp",
                    },
                    "emoji": {
                        "type": "string",
                        "description": "Emoji name (e.g., 'thumbsup', 'eyes', 'white_check_mark')",
                    },
                    "workspace": {
                        "type": "string",
                        "description": "Workspace key (optional)",
                    },
                },
                "required": ["channel", "timestamp", "emoji"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool calls."""
    try:
        result = await _handle_tool(name, arguments)
        return [TextContent(type="text", text=result)]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}")]


async def _handle_tool(name: str, args: dict[str, Any]) -> str:
    """Route tool calls to handlers."""
    workspace = args.get("workspace")

    if name == "slack_workspaces":
        config = get_config()
        workspaces = config.list_workspaces()
        lines = ["Configured Slack workspaces:", ""]
        for ws in workspaces:
            default_marker = " (default)" if ws.key == config.default_workspace else ""
            lines.append(f"- **{ws.key}**: {ws.name}{default_marker}")
        return "\n".join(lines)

    elif name == "slack_summary":
        hours = args.get("hours", 24)
        mode = args.get("mode", "quick")

        if mode == "quick":
            # Fast mode - just recent activity
            if workspace:
                client = get_client(workspace)
                return quick_summary(client, hours=hours)
            else:
                clients = get_all_clients()
                results = [quick_summary(c, hours=hours) for c in clients]
                return "\n\n---\n\n".join(results)
        else:
            # Full mode - detailed scan
            if workspace:
                client = get_client(workspace)
                summaries = [summarize_workspace(client, hours=hours)]
            else:
                clients = get_all_clients()
                summaries = [summarize_workspace(c, hours=hours) for c in clients]
            return format_summary_markdown(summaries)

    elif name == "slack_unread":
        client = get_client(workspace)
        hours = args.get("hours", 24)
        max_dms = args.get("max_dms", 15)
        max_channels = args.get("max_channels", 15)

        unread = client.get_unread_messages(hours=hours, max_dms=max_dms, max_channels=max_channels)

        if not unread:
            return "No unread messages."

        lines = ["Unread messages:", ""]
        for channel_name, messages in unread.items():
            lines.append(f"**{channel_name}** ({len(messages)} messages)")
            for msg in messages[:3]:
                preview = truncate_text(msg.text, 60)
                time_str = format_relative_time(msg.timestamp)
                user = msg.user_name or "someone"
                lines.append(f"  - [{user}] {time_str}: \"{preview}\"")
            if len(messages) > 3:
                lines.append(f"  - ... and {len(messages) - 3} more")
            lines.append("")

        return "\n".join(lines)

    elif name == "slack_channel":
        client = get_client(workspace)
        channel = args["channel"]
        limit = args.get("limit", 20)

        channel_id = client._resolve_channel(channel)
        messages = client.get_messages(channel_id, limit=limit)

        if not messages:
            return f"No messages in {channel}."

        lines = [f"Recent messages in {channel}:", ""]
        for msg in messages:
            user = msg.user_name or "Unknown"
            time_str = format_relative_time(msg.timestamp)
            text = msg.text.replace("\n", " ")
            thread_info = f" (thread: {msg.reply_count} replies)" if msg.reply_count > 0 else ""
            lines.append(f"[{time_str}] **{user}**{thread_info}: {text}")
            lines.append(f"  _ts: {msg.ts}_")
            lines.append("")

        return "\n".join(lines)

    elif name == "slack_dm":
        client = get_client(workspace)
        person = args["person"]
        limit = args.get("limit", 20)

        channel_id, resolved_name = client.find_dm_by_person(person)
        messages = client.get_messages(channel_id, limit=limit)

        if not messages:
            return f"No messages with {resolved_name}."

        lines = [f"DM conversation with {resolved_name}:", ""]
        for msg in messages:
            # Determine if this is from the other person or me
            if msg.user_id == client.my_user_id:
                sender = "You"
            else:
                sender = resolved_name.lstrip("@")
            time_str = format_relative_time(msg.timestamp)
            text = msg.text.replace("\n", " ")
            lines.append(f"[{time_str}] **{sender}**: {text}")
            lines.append(f"  _ts: {msg.ts}_")
            lines.append("")

        return "\n".join(lines)

    elif name == "slack_thread":
        client = get_client(workspace)
        channel = args["channel"]
        thread_ts = args["thread_ts"]

        channel_id = client._resolve_channel(channel)
        messages = client.get_thread(channel_id, thread_ts)

        if not messages:
            return "No messages in thread."

        lines = [f"Thread in {channel}:", ""]
        for msg in messages:
            user = msg.user_name or "Unknown"
            time_str = format_relative_time(msg.timestamp)
            text = msg.text.replace("\n", " ")
            lines.append(f"[{time_str}] **{user}**: {text}")
            lines.append("")

        return "\n".join(lines)

    elif name == "slack_search":
        client = get_client(workspace)
        query = args["query"]
        count = args.get("count", 20)

        messages = client.search_messages(query, count=count)

        if not messages:
            return f"No results for: {query}"

        lines = [f"Search results for '{query}':", ""]
        for msg in messages:
            user = msg.user_name or "Unknown"
            time_str = format_relative_time(msg.timestamp)
            text = truncate_text(msg.text, 80)
            lines.append(f"**{msg.channel_name}** - {user} ({time_str})")
            lines.append(f"  {text}")
            lines.append(f"  _ts: {msg.ts}_")
            lines.append("")

        return "\n".join(lines)

    elif name == "slack_channels":
        client = get_client(workspace)
        filter_type = args.get("type", "channels")

        if filter_type == "all":
            types = "public_channel,private_channel,mpim,im"
        elif filter_type == "dms":
            types = "im,mpim"
        elif filter_type == "groups":
            types = "private_channel,mpim"
        else:  # channels
            types = "public_channel,private_channel"

        conversations = client.get_conversations(types=types)

        if not conversations:
            return "No channels found."

        lines = [f"Channels ({len(conversations)}):", ""]

        # Group by type
        by_type: dict[str, list] = {}
        for conv in conversations:
            if conv.type not in by_type:
                by_type[conv.type] = []
            by_type[conv.type].append(conv)

        type_labels = {
            "channel": "Public Channels",
            "group": "Private Channels",
            "dm": "Direct Messages",
            "mpim": "Group DMs",
        }

        for conv_type, convs in by_type.items():
            label = type_labels.get(conv_type, conv_type)
            lines.append(f"### {label} ({len(convs)})")
            lines.append("")
            for conv in sorted(convs, key=lambda c: c.name.lower()):
                lines.append(f"- {conv.name} (id: `{conv.id}`)")
            lines.append("")

        return "\n".join(lines)

    elif name == "slack_send":
        client = get_client(workspace)
        channel = args["channel"]
        text = args["text"]
        reply_to_ts = args.get("reply_to_ts")

        msg = client.send_message(channel, text, context_for_ts=reply_to_ts)
        return f"Message sent to {channel} (ts: {msg.ts})"

    elif name == "slack_reply":
        client = get_client(workspace)
        channel = args["channel"]
        thread_ts = args["thread_ts"]
        text = args["text"]
        reply_to_ts = args.get("reply_to_ts")

        msg = client.send_message(channel, text, thread_ts=thread_ts, context_for_ts=reply_to_ts)
        return f"Reply sent (ts: {msg.ts})"

    elif name == "slack_react":
        client = get_client(workspace)
        channel = args["channel"]
        timestamp = args["timestamp"]
        emoji = args["emoji"]

        success = client.add_reaction(channel, timestamp, emoji)
        if success:
            return f"Added :{emoji}: reaction"
        return f"Failed to add reaction"

    else:
        return f"Unknown tool: {name}"


async def run_server():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main():
    """Entry point."""
    import asyncio
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
