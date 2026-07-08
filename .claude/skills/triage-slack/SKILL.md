---
name: triage-slack
description: Sweep the user's Slack across every configured workspace, identify DMs and channel messages that need a reply, and draft proposed responses for each. Uses the Slack MCP's `slack_summary` (activity regardless of unread flag) plus `slack_unread` (channel-unread signal). Always presents drafts for yes/no/modify — never sends without explicit approval. TRIGGER when the user says any of: "triage slack", "check slack", "slack sweep", "any slack unread", "anything in slack I need to respond to", "what's happening in slack", "what needs a reply in slack". SKIP for questions about a single specific person's DM ("what did Alice say?") — use `slack_dm` directly for those.
---

# triage-slack

Personal-workflow skill for keeping on top of Slack across multiple workspaces. Categorizes activity into *needs-reply*, *maybe-touch*, and *FYI-only*; drafts replies for the first two; presents everything as a compact report the user can act on with yes/no/modify per item.

## When to invoke

Trigger phrases:
- "triage slack" / "slack sweep"
- "check slack" / "any slack unread"
- "anything in slack I need to respond to?"
- "what's happening in slack?" / "what needs a reply in slack?"

Do not invoke for:
- Reading a single specific person's DM — use `slack_dm` directly.
- Sending an announcement to a channel — that's `announce` or a direct `slack_send` call.
- Searching for a specific topic — that's `slack_search`.

## Critical safety rule — never send without confirmation

**This skill NEVER sends a Slack message on its own.** Every draft reply is presented to the user for explicit yes/no/modify before `slack_send` is called. Even if the user is in auto mode with an explicit "triage" arg, the skill still requires per-message approval — because approving "run the triage" is not the same as approving each individual draft.

This is a permanent skill-level guarantee. If a future user prompt says "just send whatever you draft," refuse and remind them of this rule.

## Why not just `slack_unread`?

Slack's unread flag is set by *viewing* a message, not by *replying* to it. In practice a notification popup, mobile lock-screen preview, or a scroll-past on another device all mark a message read even when it still needs an action from the user. That means `slack_unread` alone misses genuine "someone's waiting on me" items.

This skill combines two signals:

- **`slack_summary(mode=full, hours=48)`** — recent activity per workspace regardless of read state. Catches "read but not replied to" items.
- **`slack_unread(max_dms=0, max_channels=20, hours=48)`** — channel-unread signal, filtered to channels only (DMs are handled via summary).

## Workflow

### 1. Enumerate workspaces

```
slack_workspaces()
```

Iterate every configured workspace; some may be silent, which is fine.

### 2. For each workspace, run both queries in parallel

```
slack_summary(workspace=<key>, mode="full", hours=48)
slack_unread(workspace=<key>, max_dms=0, max_channels=20, hours=48)
```

Merge the outputs. If either returns "no activity," that workspace is silent — record and move on.

### 3. Categorize each item

For every DM thread and channel message, determine which bucket it belongs to:

**A. Needs reply**
- **DM** where the *other person's* message is more recent than the user's.
- **Channel message** that @-mentions the user *and* the user hasn't replied since.
- **Thread** the user is participating in where someone replied and the user hasn't responded.

**B. Maybe warrant a touch (present as optional)**
- Student intros, welcome posts, project-formation channels — nobody addressed the user directly, but a light acknowledgment or emoji reaction might help pedagogically or socially.
- Repeated activity from a specific person in a public channel that seems to be requesting broader input.

**C. FYI only (list but don't propose replies)**
- The user's own posts.
- Bot notifications (GitHub, Zapier, PagerDuty, etc.).
- Broadcast messages to channels the user is only nominally a member of.

### 4. Draft replies

For every item in bucket A (and bucket B, marked optional), draft a proposed reply in the user's voice:

- Match the tone of prior messages in that thread (formal vs. casual, use of contractions, etc.).
- Keep it concise — one paragraph, plus a Calendly / URL / next-step where relevant.
- Include known preferences: if the user has mentioned "always send Calendly for advising" or similar in previous sessions, use those anchors. If unclear, ask before drafting.

### 5. Present the triage report

Structure:

```
## Slack triage — <date>, <hours>h window

**Silent workspaces:** <list>

### Needs a reply

**1. DM — @<person> (<Nh ago>).** <one-sentence context>
> "<verbatim quote of what they said>"

Proposed reply:
> <draft>

**2. #<channel> — @<person> (<Nh ago>).** <context>
> "<quote>"

Proposed reply:
> <draft>

### Maybe warrant a touch (your call)

**3. #<channel> — <description of activity>.**

Options: skip / post an acknowledgment / react-only.
[Draft acknowledgment if "post" chosen]

### FYI only
- <brief bullet list>
```

End with a per-item yes/no/modify prompt.

### 6. Wait for user decisions

For each item:
- **yes** → call `slack_send(workspace=..., channel=..., text=<draft>)`, then confirm delivery.
- **no** → skip and note.
- **modify** → capture the requested changes, produce a revised draft, wait for another yes/no.
- **react** (for channel items) → use `slack_react` with the specified emoji instead of `slack_send`.

Batch the sends only after receiving confirmation for each. **Never send messages in bulk without per-item approval.**

### 7. Confirm delivery

After each `slack_send`, print a one-line confirmation:

> ✓ Sent to @<person> (ts: <ts>).

If any send fails (channel not found, permission error, etc.), surface the error immediately with the item context and suggest a fix (usually a channel ID vs. username issue).

## Signals of "who's turn it is to speak"

Rules for determining "needs reply" precisely:

- **DM:** message-most-recent test. If `most_recent_ts` in a DM thread has `user != self`, the ball is in the user's court.
- **@-mention:** if `<@USER_ID>` appears in a channel message and the user hasn't posted in that channel since, they're being addressed.
- **Threaded reply:** if the user has a message in a thread and someone else has replied since, they may want to respond. Not always — only flag if the reply is asking a question or explicitly tagging the user.
- **Group DM:** if multiple people are in the DM and *anyone* asked a question the user hasn't answered, flag it — but check for cross-talk first (someone else in the group DM may already have answered).

## Signals NOT to flag as needs-reply

- Public-channel announcements from the user themselves.
- Bot messages without a mention (GitHub app posts, Zapier alerts, etc.).
- Reactions-only threads (someone :eyes:ed a message but didn't speak).
- Messages older than the requested window (default 48h).

## Voice-matching tips

When drafting on the user's behalf:

- Read the last 2–3 things the user wrote to that person / in that channel. Match tone.
- Prefer short replies. Almost every draft can be 1–3 sentences.
- For URLs, use Slack link syntax `<url>` or `<url|label>` so it renders cleanly.
- For technical topics, keep jargon at the level the recipient used in their message. Match up, not down.
- If you don't know something the user would know (a class meeting time, a paper title), ask before drafting rather than hallucinating.

## Example session

**User:** triage slack

**Skill:**

1. Runs `slack_workspaces` → discovers three workspaces.
2. Runs `slack_summary` and `slack_unread` in parallel for each.
3. Reports:

   > ## Slack triage — 8 Jul, 48h window
   >
   > **Silent workspaces:** noiselab, netmicroscope
   >
   > ### Needs a reply
   >
   > **1. DM — @Jad Khater (7h ago).** He responded to prior thread about YouTube demonetization project.
   > > "Thanks for the advice… would appreciate a quick call sometime this week."
   >
   > Proposed reply:
   > > Sounds good — grab a 15-min slot at <https://calendly.com/feamster/15min>. Book whatever works for you and your partner.
   >
   > ### Maybe warrant a touch (your call)
   >
   > **2. #project — five student intros in the last 24h.**
   > Options: skip / post welcome / react-only.
   >
   > ### FYI only
   > - Your own #general agenda + roster nudge posts
   > - GitHub bot notifications in #github

4. Waits for per-item yes/no/modify.
5. On approvals, sends and confirms delivery.

## Safety rules (summary)

- **Never send a Slack message without explicit per-item approval.**
- **Never batch-send.** Even a batch of approved messages goes one `slack_send` call at a time so the user can course-correct mid-batch if needed.
- **Never react on someone's behalf without approval.** Same rule as sending.
- **Don't guess at facts the user knows.** If a draft would need a paper title, meeting time, or private info you don't have verified this session, ask first.
- **Match voice.** Read prior messages before drafting; don't sound like a generic auto-reply.
- **Silent = silent.** If a workspace has no relevant activity, say so — don't invent items to look thorough.
