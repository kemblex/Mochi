import discord
import os
import datetime
from dotenv import load_dotenv

# setup
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(client)

# ------------------------------------------------------------------------------
# on startup
@client.event
async def on_ready():
    synced = await tree.sync()
    print(f"Synced {len(synced)} command(s)")
    print(f"{client.user} is online!")

# ------------------------------------------------------------------------------
# Helpers
weekdays = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2, "weds": 2, "wedn": 2, "reset": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6
}

def days_until_weekday(now, target_weekday):
    today_weekday = now.weekday()
    days_ahead = (target_weekday - today_weekday) % 7
    if days_ahead == 0: days_ahead = 7
    return days_ahead

schedule = {}

# ------------------------------------------------------------------------------
# Commands
@tree.command(name="hello", description="Say hello to Mochi")
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message("Hello!")

@tree.command(name="mtime", description="Maplestory Boss Scheduler")
async def mtime(
    interaction: discord.Interaction, 
    time: str,
    name: str = None
    ):
    parts = time.lower().split()
    
    hours = 0
    
    try:
        hours = float(parts[-1])
    except ValueError:
        pass
        
    now = discord.utils.utcnow()
    midnight = now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )
    
    if parts[0] == "today":
        target_time = midnight + datetime.timedelta(days=1, hours=hours)
        
    elif parts[0] == "tomorrow":
        target_time = midnight + datetime.timedelta(days=2, hours=hours)
    
    elif parts[0] == "next" and len(parts) > 1 and parts[1] in weekdays:
        target_weekday = weekdays[parts[1]]
        days_ahead = days_until_weekday(now, target_weekday) + 7
        target_time = midnight + datetime.timedelta(days=days_ahead+1, hours=hours)
    
    elif parts[0] in weekdays:
        target_weekday = weekdays[parts[0]]
        days_ahead = days_until_weekday(now, target_weekday) 
        target_time = midnight + datetime.timedelta(days=days_ahead+1, hours=hours)
    
    else:
        try:
            hours = float(parts[0])
            target_time = midnight + datetime.timedelta(days=1, hours=hours)
        except ValueError:
            try:
                date = datetime.datetime.strptime(parts[0], "%m/%d")
                date = date.replace(year=now.year, tzinfo=datetime.timezone.utc)
                
                if date < midnight:
                    date = date.replace(year=now.year + 1)
                    
                target_time = date + datetime.timedelta(days=1, hours=hours)
            
            except ValueError:
                await interaction.response.send_message(
                    "Invalid time! Try `+2`, `tomorrow +2`, `monday +2`, `next monday +2`, or `mm/dd +2`."
                    )
                return
            
    timestamp = int(target_time.timestamp())
    
    if name is not None:
        schedule[name.lower()] = timestamp
        await interaction.response.send_message(
            f"{name.title()} - <t:{timestamp}:F>"
            )
    else:
        await interaction.response.send_message(f"<t:{timestamp}:F>")

@tree.command(name="mschedule", description="Show saved boss times")
async def show_schedule(interaction: discord.Interaction):
    if not schedule:
        await interaction.response.send_message("No bosses scheduled.")
        return
    
    sorted_schedule = sorted(
        schedule.items(),
        key=lambda item: item[1]
    )
    
    message = "**Boss Schedule:**\n"
    
    for i, (name, timestamp) in enumerate(sorted_schedule, start = 1):
        message += f"{i}. {name.title()} - <t:{timestamp}:F>\n"
        
    await interaction.response.send_message(message)

@tree.command(name="mremoveboss", description="Remove a boss time from the schedule")
async def removeboss(interaction: discord.Interaction, number: int):
    if not schedule:
        await interaction.response.send_message("No bosses scheduled.")
        return

    sorted_schedule = sorted(
        schedule.items(),
        key=lambda item: item[1]
    )

    if number < 1 or number > len(sorted_schedule):
        await interaction.response.send_message("Invalid boss number!")
        return

    name, timestamp = sorted_schedule[number - 1]

    del schedule[name]

    await interaction.response.send_message(
        f"Removed {name.title()} — <t:{timestamp}:F>"
    )

# ------------------------------------------------------------------------------
client.run(TOKEN)

# source .venv/bin/activate
# python bot.py