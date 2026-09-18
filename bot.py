import datetime
import discord
import os
import secrets
import sqlite3
import time
from dotenv import load_dotenv
from aiohttp import web
from time_parser import parse_mtime
from schedule_store import (initialize_schedule, remove_owned_event,
                            participating_events, participating_event, leave_event)
from schedule_ui import ScheduleView, ConfirmRemoval, refresh_open_schedules

# =============================================================================
# SHARED SETUP — environment, Discord client, and database
# =============================================================================
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

class MochiClient(discord.Client):
    http_runner = None

    async def setup_hook(self):
        # Login setup runs once; on_ready can repeat after a reconnect.
        self.http_runner = await start_http_server()

    async def close(self):
        try:
            if self.http_runner is not None:
                await self.http_runner.cleanup()
                self.http_runner = None
        finally:
            await super().close()


intents = discord.Intents.default()
client  = MochiClient(intents=intents)
tree    = discord.app_commands.CommandTree(client)

db      = sqlite3.connect("mochi.db")

db.execute("""
    CREATE TABLE IF NOT EXISTS users (
        discord_user_id INTEGER PRIMARY KEY,
        ocr_channel_id INTEGER
    )
""")

db.commit()
initialize_schedule(db)


# =============================================================================
# SHARED DISCORD EVENTS AND GENERAL COMMANDS
# =============================================================================

@client.event
async def on_ready():
    synced = await tree.sync()
    print(f"Synced {len(synced)} command(s)")
    print(f"{client.user} is online!")

@tree.command(name="hello", description="Say hello to Mochi")
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"Hello {interaction.user.mention}!", allowed_mentions=discord.AllowedMentions.none()
        )


# =============================================================================
# SCHEDULING — timestamp and event commands
# Parsing: time_parser.py | Storage: schedule_store.py | UI: schedule_ui.py
# =============================================================================

@tree.command(name="mtime", description="Maplestory relative time command")
async def mtime(
    interaction: discord.Interaction,
    time: str,
    name: str = None
    ):
    try:
        timestamp = parse_mtime(time)
    except ValueError:
        await interaction.response.send_message(
            "Invalid time! Try `+2`, `tomorrow +1.5`, `monday +2`, `next monday +2`, or `mm/dd +2`."
        )
        return
    
    if name is not None:
        label = name.strip().title()
        message = f":smiling_imp: **`{label}`**\n<t:{timestamp}:F> • <t:{timestamp}:R>"
    else:
        message = f"<t:{timestamp}:F> • <t:{timestamp}:R>"

    await interaction.response.send_message(
        message,
        allowed_mentions=discord.AllowedMentions.none()
    )


@tree.command(name="mschedule", description="View your events and create new ones")
async def show_schedule(interaction: discord.Interaction):
    view = ScheduleView(db, interaction.user.id)
    await interaction.response.send_message(
        embed=view.embed(), view=view, allowed_mentions=discord.AllowedMentions.none()
    )
    view.message = await interaction.original_response()


@tree.command(name="mleave", description="Leave one of your scheduled events")
@discord.app_commands.describe(event="Search and select an event you participate in")
async def leave_schedule_event(interaction: discord.Interaction, event: str):
    try:
        event_id = int(event)
        if not 0 < event_id < 2**63:
            raise ValueError
    except ValueError:
        await interaction.response.send_message('Select an event from the suggestions.', ephemeral=True)
        return
    row = participating_event(db, event_id, interaction.user.id)
    if row is None:
        await interaction.response.send_message('Event unavailable or you are no longer a participant.', ephemeral=True)
        return
    result = leave_event(db, event_id, interaction.user.id)
    if result == 'owner_confirmation_required':
        schedule = ScheduleView(db, interaction.user.id)
        await interaction.response.send_message(
            f'You own **{discord.utils.escape_markdown(row[2])}** (<t:{row[3]}:F>). '
            'Leaving will cancel this event for everyone. Confirm cancellation?',
            view=ConfirmRemoval(schedule, event_id), ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return
    message = (f'You left **{discord.utils.escape_markdown(row[2])}**. The event remains for the other participants.'
               if result == 'left' else 'Event unavailable or you are no longer a participant.')
    await interaction.response.send_message(message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    if result == 'left':
        await refresh_open_schedules(db)


@leave_schedule_event.autocomplete('event')
async def leave_event_choices(interaction: discord.Interaction, current: str):
    choices = []
    for event_id, owner_id, name, timestamp in participating_events(db, interaction.user.id):
        when = datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc).strftime('%b %d, %Y %H:%M UTC')
        # Keep the date visible even with a long custom name. Values use stable IDs.
        label = f'{name[:60]} — {when}'
        if current.casefold() in label.casefold():
            choices.append(discord.app_commands.Choice(name=label, value=str(event_id)))
        if len(choices) == 25:
            break
    return choices


@tree.command(name="mremoveboss", description="Remove an event you own by its schedule ID")
@discord.app_commands.describe(number="The event ID shown after # in /mschedule")
async def removeboss(interaction: discord.Interaction, number: int):
    removed = remove_owned_event(db, number, interaction.user.id)
    if removed is None:
        await interaction.response.send_message("Event not found, or you are not its owner.", ephemeral=True)
        return
    name, timestamp = removed
    await interaction.response.send_message(
        f"Removed #{number}: {name} — <t:{timestamp}:F>",
        allowed_mentions=discord.AllowedMentions.none(),
    )


# =============================================================================
# OCR — HTTP helpers, pairing, and text forwarding
# =============================================================================

PAIRING_TTL = 300  # Five minutes; unused codes disappear when Mochi restarts.
pairing_codes = {}


def api_error(status, error, message, **details):
    return web.json_response(
        {"ok": False, "error": error, "message": message, **details}, status=status
    )


async def read_json_object(request):
    try:
        data = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise web.HTTPBadRequest(
            text='{"ok": false, "error": "invalid_json", "message": "Send a JSON object."}',
            content_type="application/json",
        )
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(
            text='{"ok": false, "error": "invalid_json", "message": "Send a JSON object."}',
            content_type="application/json",
        )
    return data


async def receive_pair(request):
    data = await read_json_object(request)
    code = data.get("code")
    if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit():
        return api_error(400, "invalid_code", "Send the six-digit pairing code as a string.")

    # No await between checking and consuming: only one request can use a code.
    pairing = pairing_codes.pop(code, None)
    if pairing is None or pairing[1] <= time.monotonic():
        return api_error(400, "invalid_code", "Code is invalid or expired. Run /mpair again.")

    return web.json_response({"ok": True, "type": "pair_success", "user_id": pairing[0]})


# Local prototype: a supplied user ID is identification, not authentication.
async def receive_event(request):
    data = await read_json_object(request)
    if data.get("type") != "chat_text":
        return api_error(400, "unsupported_type", "Expected event type chat_text.")

    user_id = data.get("user_id")
    if isinstance(user_id, str) and user_id.isascii() and user_id.isdigit() and len(user_id) <= 19:
        user_id = int(user_id)
    if type(user_id) is not int or not 0 < user_id < 2**63:
        return api_error(400, "invalid_user_id", "Provide a positive Discord user ID.")

    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        return api_error(400, "invalid_text", "Provide non-empty text.")
    if len(text) > 20000:
        return api_error(400, "text_too_long", "Send at most 20000 characters per event.")

    if not client.is_ready():
        return api_error(503, "discord_not_ready", "Mochi is not connected to Discord yet.")

    # Read settings for every event, so changing channels never requires pairing again.
    row = db.execute(
        "SELECT ocr_channel_id FROM users WHERE discord_user_id = ?", (user_id,)
    ).fetchone()
    if row is None or row[0] is None:
        return api_error(409, "channel_not_set", "Run /msetocrchannel in your output channel.")

    sent = 0
    try:
        channel = client.get_channel(row[0])
        if channel is None:
            channel = await client.fetch_channel(row[0])
        if not isinstance(channel, discord.abc.Messageable):
            return api_error(400, "invalid_channel", "Select a channel that accepts messages.")

        for offset in range(0, len(text), 2000):
            chunk = text[offset:offset + 2000]
            if chunk.strip():
                await channel.send(chunk, allowed_mentions=discord.AllowedMentions.none())
                sent += 1
    except discord.Forbidden:
        return api_error(403, "discord_forbidden", "Mochi cannot access or send to that channel.", messages_sent=sent)
    except discord.NotFound:
        return api_error(404, "channel_not_found", "Select an existing channel with /msetocrchannel.", messages_sent=sent)
    except (discord.HTTPException, OSError, TimeoutError):
        return api_error(502, "delivery_failed", "Delivery failed or is uncertain; do not automatically retry.", messages_sent=sent)

    return web.json_response({"ok": True, "type": "ack", "messages_sent": sent})

# Create HTTP server
async def start_http_server():
    app = web.Application()

    app.router.add_post("/event", receive_event)
    app.router.add_post("/pair", receive_pair)

    runner = web.AppRunner(app)
    await runner.setup()

    try:
        site = web.TCPSite(runner, "127.0.0.1", 8765)
        await site.start()
    except Exception:
        await runner.cleanup()
        raise

    print("Mochi HTTP server started on http://127.0.0.1:8765")
    return runner


# =============================================================================
# OCR — Discord commands
# =============================================================================

@tree.command(name="mpair", description="Pair screen-ocr with your Discord account")
async def pair(interaction: discord.Interaction):
    now = time.monotonic()
    for code, (user_id, expires_at) in list(pairing_codes.items()):
        if expires_at <= now or user_id == interaction.user.id:
            del pairing_codes[code]

    code = str(secrets.randbelow(900000) + 100000)
    while code in pairing_codes:
        code = str(secrets.randbelow(900000) + 100000)
    pairing_codes[code] = (interaction.user.id, now + PAIRING_TTL)
    await interaction.response.send_message(
        f"Your pairing code is `{code}`. It expires in 5 minutes and can be used once.",
        ephemeral=True,
    )


@tree.command(name="msetocrchannel", description="Set your OCR output channel")
async def set_ocr_channel(interaction: discord.Interaction):
    user_id = interaction.user.id
    channel_id = interaction.channel_id

    # Save this user's selected channel
    db.execute("""
        INSERT INTO users (discord_user_id, ocr_channel_id)
        VALUES (?, ?)
        ON CONFLICT(discord_user_id)
        DO UPDATE SET ocr_channel_id = excluded.ocr_channel_id
    """, (user_id, channel_id))

    db.commit()

    await interaction.response.send_message(
        f"Your OCR channel has been set to <#{channel_id}>."
    )


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    client.run(TOKEN)

# source .venv/bin/activate
# python bot.py
