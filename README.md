# Mochi

A custom Discord bot for scheduling Maplestory bosses across different time zones.
Maplestory reset time is UTC midnight of the next day. We use +2 to indicate 2 hours after reset time.

## Commands

- `/mtime`        - Create a discord timestamp based on Maplestory reset time
    - Try `+2`, `tomorrow +2`, `monday +2`, `next monday +2`, or `mm/dd +2` with optional `name`
- `/mschedule`    - Show saved boss times
- `/mremove boss` - Remove a boss from the schedule
- `/hello`        - Say hello to Mochi
- `/msetocrchannel` - Save the current channel as your OCR output channel
- `/mpair` - Privately generate a single-use pairing code valid for five minutes

## Local HTTP text forwarding

Run Mochi with `python bot.py` from this folder. Its HTTP server starts at
`http://127.0.0.1:8765`; this does not start screen capture. SQLite settings
are stored in `mochi.db` in the working directory. The scheduler is unchanged.

This is a local prototype: a supplied Discord user ID is not authentication.
Keep the listener on loopback. Installation tokens and HTTPS are required
before exposing this interface publicly. The screen-ocr HTTP sender still
needs to be updated separately.

### Pair once

Run `/mpair`, then exchange the code:

```bash
curl -X POST http://127.0.0.1:8765/pair \
  -H 'Content-Type: application/json' \
  -d '{"code":"123456"}'
```

Replace the example code with your actual code. HTTP 200 returns:

```json
{"ok":true,"type":"pair_success","user_id":123456789012345678}
```

screen-ocr should save `user_id` locally and reuse it on future runs. Existing
saved user IDs can be reused. Codes expire after five minutes, are consumed
once, and disappear on bot restart. Running `/mpair` again replaces that user's
unused code. If the pairing response is lost, generate a new code.

### Send text

Run `/msetocrchannel` in your intended output channel, then send an event with
the paired user ID (a JSON integer or a decimal string is accepted):

```bash
curl -X POST http://127.0.0.1:8765/event \
  -H 'Content-Type: application/json' \
  -d '{"type":"chat_text","user_id":123456789012345678,"text":"hello"}'
```

HTTP 200 means Discord accepted all message chunks:

```json
{"ok":true,"type":"ack","messages_sent":1}
```

Each event looks up the user's current channel in SQLite. Changing it with
`/msetocrchannel` never requires re-pairing. Mentions are disabled. Non-empty
text up to 20,000 characters is accepted and split into chunks of at most
2,000 characters; whitespace-only chunks are skipped.

Handler errors return `{"ok":false,"error":"...","message":"..."}`:

| HTTP status | Meaning |
| --- | --- |
| 400 | Invalid JSON/fields, invalid or expired code, unsupported event, or unsuitable channel |
| 403 | Mochi lacks Discord access or send permissions |
| 404 | The selected Discord channel no longer exists |
| 409 | No output channel has been selected |
| 502 | Discord delivery failed or its outcome is uncertain |
| 503 | Mochi is not ready to communicate with Discord |

Delivery errors also include `messages_sent`, counting confirmed chunks. Earlier
chunks may already have been posted. Keep the local OCR backup and do not
automatically retry errors or timeouts: there is no duplicate suppression yet.
Check both HTTP status and `ok`; a transport error or a non-JSON response is not
an ACK either. Pairing and events do not require a persistent connection.

## Tests

```bash
.venv/bin/python -B -m unittest discover -s tests -v
```

These tests use local HTTP sockets, an in-memory database, and mocked Discord
sends. They do not log into Discord or modify your real channel settings.

## Scheduling

- `/mtime time:friday +2 name:Limbo` only posts a labeled timestamp. It never saves an event.
- `/mschedule` shows your participating events in a paged embed. Click **Add** to open one form with a boss dropdown,
  reset-relative time, and optional custom name. Select **Custom…** to use the custom name;
  otherwise that field is ignored.
- New events persist in SQLite with stable IDs and automatic owner membership. Duplicate
  boss names and times are allowed. Successful creation refreshes the originating schedule.
- Only the user who opened the schedule can operate its controls. Other people can see
  the public embed, but must run `/mschedule` for their own controls. Controls expire after
  ten minutes or a bot restart; run the command again. Run `/mschedule` again to update other open copies.
- `/mremoveboss number:` still removes an owned event using its displayed `#ID`, without
  the `#`. Another participant cannot delete it. The visual schedule hides database IDs;
  the Edit menu below is the preferred removal path. Public Join controls and reminders
  are not implemented yet. Party viewing and Leave are described below.

Both `/mtime` and Add use `time_parser.py`. Numeric offsets, today/tomorrow, weekdays,
next weekdays, and MM/DD retain the reset convention (the following UTC midnight).
Extra words and nonnumeric offsets are rejected. No explicit year is needed or accepted:
MM/DD uses this year, or next year if the month/day has passed. February 29 is accepted
only if that inferred year is a leap year; it never skips several years ahead.

### Edit and remove owned events

Use `/mschedule` → **Edit** → select an owned event. The picker displays names and
UTC times (Discord timestamps cannot render inside select-option descriptions),
with pages for more than 25 events. The selected event's management card displays
Discord timestamps in your local timezone.

- **Edit Event** opens the existing boss/custom-name form with the current name
  selected. A blank new time preserves the saved time; a supplied time uses the
  same reset-relative parser as `/mtime`. Saving keeps the event ID and members.
- **Remove Event** opens a confirmation. **Keep event** does nothing to storage;
  **Confirm cancellation** deletes the event and all associated membership rows.

Only the owner can save or delete, and ownership/existence is checked again when
submitting. Successful changes refresh the originating schedule. Other open
schedule copies can be updated by running `/mschedule` again. Temporary controls expire; run
`/mschedule` again if needed. No database schema change is required for this flow.

### Party viewing and leaving

Use `/mschedule` → **Party** → choose an event you participate in. This includes
owned events and events joined through membership records. The private party
panel shows the host and member mentions without pinging them, with pagination
for larger member lists.

- A non-owner's **Leave event** removes only their membership and refreshes their
  originating schedule. The event and all other memberships remain intact.
- An owner's **Leave event** opens cancellation confirmation; nothing is removed
  unless they confirm. Confirmation deletes the shared event and its memberships.
- Stale controls handle deleted events or missing memberships safely.

Owners now see **Add Member** and **Remove Member** in the Party panel.
Add Member uses Discord's native user picker in server channels (up to 25 users
per selection). In DMs, Mochi explains that you must use a server to select other
users. Bot accounts are not accepted. Existing memberships are left unchanged.

Remove Member lists current members except the owner, with pages when needed.
Removing someone only deletes their membership; the shared event remains.
The picker returns to the refreshed party panel after either operation. Other
users can run `/mschedule` again to see their changed membership.
Owners cannot remove themselves this way; use Leave event and confirm cancellation.
Public event cards with Join/Leave invitations are the next step.

Schedule entries display party mentions beneath the time without pinging members.
For large parties, the schedule shows a short preview; the Party panel shows the
full paginated list. Removal menus resolve uncached users through Discord and show
display names/usernames; if lookup fails, an ID fallback remains available.
Refresh buttons have been removed. Actions refresh their originating schedule and
party panel, but other already-open messages are snapshots: run `/mschedule` again
for current data. Automatic synchronization of all messages is not implemented.

### Leave one event quickly

Use `/mleave event:` and select a suggestion. Suggestions contain only events you
participate in; type part of the name/date to filter them. Times in autocomplete
are labeled UTC because select suggestions cannot render Discord timestamps.
Non-owners leave immediately with a private confirmation. Owners receive the
existing cancellation confirmation before anything is deleted for other members.
Other open schedule messages remain snapshots; run `/mschedule` again to update.
`/mclear` is planned as a separate, confirmed bulk action after this flow is tested.

`/mleave` and confirmed cancellation now refresh active `/mschedule` messages
tracked by the current bot process, including other participants' open schedules.
Expired controls and messages from before a restart are not tracked for updates;
run `/mschedule` again for those. Other mutation paths still update their originating
schedule rather than synchronizing every copy.
