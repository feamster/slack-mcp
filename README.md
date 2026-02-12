# Slack MCP

Read and respond to Slack from Claude. Works as an MCP server (for Claude Desktop/Claude Code) and as a standalone CLI (for GitHub Actions).

## Features

- **Multi-workspace support** - Configure multiple Slack workspaces
- **Read tools** - Summarize activity, list channels, read messages, search
- **Write tools** - Send messages, reply to threads, add reactions
- **Standalone CLI** - Generate markdown summaries for GitHub Actions

## Installation

### 1. Create Slack App(s)

For each workspace you want to connect:

1. Go to https://api.slack.com/apps → **Create New App** → **From manifest**
2. Select your workspace
3. Paste this manifest:

```yaml
display_information:
  name: Claude Slack Reader
  description: Read and respond to Slack from Claude
oauth_config:
  scopes:
    user:
      - channels:history
      - channels:read
      - chat:write
      - groups:history
      - groups:read
      - im:history
      - im:read
      - mpim:history
      - mpim:read
      - users:read
settings:
  org_deploy_enabled: false
  socket_mode_enabled: false
  token_rotation_enabled: false
```

4. Click **Install to Workspace** → Allow
5. Copy the **User OAuth Token** (starts with `xoxp-`)

> **Note**: Slack requires a separate app per workspace (public distribution requires Slack approval).

### 2. Configure Tokens

Create `~/.mcp-auth/slack/config.json`:

```json
{
  "workspaces": {
    "work": {
      "name": "Work Slack",
      "token": "xoxp-your-token-here",
      "priority": 1
    },
    "research": {
      "name": "Research Group",
      "token": "xoxp-your-token-here",
      "priority": 2
    }
  },
  "default_workspace": "work"
}
```

Or use environment variables:
```bash
export SLACK_USER_TOKEN=xoxp-...           # Single workspace
export SLACK_TOKEN_WORK=xoxp-...           # Multiple workspaces
export SLACK_TOKEN_RESEARCH=xoxp-...
```

### 3. Install Dependencies

```bash
cd ~/src/slack-mcp
pip install -r requirements.txt
```

### 4. Add to Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "slack": {
      "command": "python3",
      "args": ["/path/to/slack-mcp/server.py"]
    }
  }
}
```

Restart Claude Desktop.

## Project Structure

```
slack-mcp/
├── src/
│   ├── config.py          # Multi-workspace configuration
│   ├── slack_client.py    # Slack API wrapper (read + write)
│   ├── summarizer.py      # Message categorization
│   └── mcp_server.py      # MCP server with tools
├── server.py              # Entry point for MCP server
├── slack_summary.py       # Standalone CLI
├── requirements.txt
├── pyproject.toml
└── .github/
    └── workflows/
        └── daily-summary.yml
```

## MCP Tools

### Read Tools

| Tool | Description |
|------|-------------|
| `slack_summary` | Overview of DMs, mentions, channel activity. Use `mode: "quick"` (default) or `mode: "full"` |
| `slack_channels` | List all channels (filter by type: `all`, `channels`, `dms`, `groups`) |
| `slack_channel` | Read messages from a specific channel |
| `slack_thread` | Read messages in a thread |
| `slack_search` | Search messages |
| `slack_unread` | Get unread message counts |
| `slack_workspaces` | List configured workspaces |

### Write Tools

| Tool | Description |
|------|-------------|
| `slack_send` | Send message to channel or DM |
| `slack_reply` | Reply in a thread |
| `slack_react` | Add emoji reaction |

### Example Usage in Claude

```
"What's happening in my Slack?"
→ Uses slack_summary with quick mode

"Show me the #engineering channel"
→ Uses slack_channel

"Reply to that thread saying I'll review it tomorrow"
→ Uses slack_reply

"Add a thumbsup to that message"
→ Uses slack_react
```

## Standalone CLI

Generate markdown summaries without Claude:

```bash
# Print summary to stdout
python slack_summary.py

# Save to file
python slack_summary.py --output slack-summary.md

# Look back 48 hours
python slack_summary.py --hours 48

# Specific workspace
python slack_summary.py --workspace work

# Action items only
python slack_summary.py --action-items-only
```

## GitHub Actions Workflow

The included workflow runs daily and commits a summary:

```yaml
# .github/workflows/daily-summary.yml
name: Daily Slack Summary

on:
  schedule:
    - cron: '0 12 * * *'  # 7 AM EST
  workflow_dispatch:

jobs:
  summarize:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - env:
          SLACK_USER_TOKEN: ${{ secrets.SLACK_USER_TOKEN }}
        run: python slack_summary.py --output slack-summary.md
      - run: |
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git config user.name "github-actions[bot]"
          git add slack-summary.md
          git diff --quiet --staged || git commit -m "Daily Slack summary [skip ci]"
          git push
```

Add your token as a repository secret: **Settings → Secrets → Actions → New repository secret** → `SLACK_USER_TOKEN`

## Configuration Sync

To sync config across machines, symlink to a cloud folder:

```bash
mkdir -p ~/.mcp-auth/slack
ln -sf ~/Dropbox/mcp-auth/slack/config.json ~/.mcp-auth/slack/config.json
# or Box, iCloud, etc.
```

## Performance

- **Quick mode** (default): ~4 seconds - scans recent DMs and channels
- **Full mode**: ~20 seconds - detailed scan of all activity
- **Cached calls**: ~1.5 seconds - conversation list cached for 5 minutes

## Requirements

- Python 3.10+
- `slack-sdk` - Slack API client
- `mcp` - Model Context Protocol server
