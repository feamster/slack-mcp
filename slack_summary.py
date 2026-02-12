#!/usr/bin/env python3
"""
Generate a Slack summary markdown file.

This script can be run standalone or via GitHub Actions to generate
a daily summary of Slack activity.
"""

import argparse
import sys
from datetime import datetime, timezone

from src.config import get_config
from src.slack_client import SlackClient
from src.summarizer import format_summary_markdown, summarize_workspace


def main():
    """Generate Slack summary."""
    parser = argparse.ArgumentParser(
        description="Generate a Slack activity summary",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate summary to stdout
  %(prog)s

  # Save to file
  %(prog)s --output slack-summary.md

  # Look back 48 hours
  %(prog)s --hours 48

  # Specific workspace only
  %(prog)s --workspace work
        """,
    )

    parser.add_argument(
        "--output", "-o",
        type=str,
        help="Output file (default: stdout)",
    )

    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Hours to look back (default: 24)",
    )

    parser.add_argument(
        "--workspace", "-w",
        type=str,
        help="Specific workspace to summarize (default: all)",
    )

    parser.add_argument(
        "--action-items-only",
        action="store_true",
        help="Only show action items",
    )

    args = parser.parse_args()

    try:
        config = get_config()
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Get workspaces to process
    if args.workspace:
        try:
            ws = config.get_workspace(args.workspace)
            workspaces = [ws]
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        workspaces = config.list_workspaces()

    print(f"Fetching Slack activity from last {args.hours} hours...", file=sys.stderr)

    summaries = []
    for ws in workspaces:
        print(f"  Processing {ws.name}...", file=sys.stderr)
        client = SlackClient(workspace=ws)
        summary = summarize_workspace(client, hours=args.hours)
        summaries.append(summary)

    # Generate markdown
    if args.action_items_only:
        # Custom format for action items only
        output = generate_action_items_only(summaries)
    else:
        output = format_summary_markdown(summaries)

    # Write output
    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"Summary written to {args.output}", file=sys.stderr)
    else:
        print(output)


def generate_action_items_only(summaries):
    """Generate a summary showing only action items."""
    now = datetime.now(timezone.utc)
    lines = [
        f"# Slack Action Items - {now.strftime('%B %d, %Y')}",
        "",
    ]

    has_items = False
    for ws in summaries:
        if ws.action_items:
            has_items = True
            if len(summaries) > 1:
                lines.append(f"## {ws.name}")
                lines.append("")

            lines.append("| From | Channel | Message | Time |")
            lines.append("|------|---------|---------|------|")

            from src.summarizer import truncate_text, format_relative_time
            for msg in ws.action_items:
                from_name = msg.user_name or "Unknown"
                text = truncate_text(msg.text, 60)
                time_str = format_relative_time(msg.timestamp)
                lines.append(f"| {from_name} | {msg.channel_name} | {text} | {time_str} |")

            lines.append("")

    if not has_items:
        lines.append("No action items requiring your attention.")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    main()
