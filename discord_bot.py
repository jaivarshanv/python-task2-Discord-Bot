import discord
import os
import sys
import asyncio
import re  # For regex extraction fallback
import google.generativeai as genai
import google.api_core.exceptions
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import pytz
import dateparser
from dateparser.search import search_dates

# Reconfigure stdout to use UTF-8 so emojis print correctly
if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# Load API keys (replace with your actual keys)
DISCORD_BOT_TOKEN = ""#enter your own keys
GEMINI_API_KEY = ""

# Configure the Gemini API via the google.generativeai library
genai.configure(api_key=GEMINI_API_KEY)
# Initialize the model (adjust the model name if needed)
model = genai.GenerativeModel("gemini-1.5-pro-latest")

# Set up the Discord bot with required intents
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Dictionaries for storing user time zones, reminders, and AI mode preferences
user_timezones = {}     # { user_id: timezone_str }
reminders = {}          # { user_id: list of tuples (reminder_datetime, message) }
user_ai_mode = {}       # { user_id: bool }  (True means auto-AI response enabled)

# ----- Admin Commands -----
@bot.command()
async def restart(ctx):
    if not ctx.author.guild_permissions.administrator:
        await ctx.send("❌ You don't have permission to restart the bot.")
        return
    await ctx.send("🔄 Restarting...")
    os.execv(sys.executable, ["python"] + sys.argv)

@bot.command()
async def stop(ctx):
    if ctx.author.guild_permissions.administrator:
        await ctx.send("⚠️ Shutting down... Goodbye! 👋")
        await bot.close()
        await asyncio.sleep(1)
        sys.exit(0)
    else:
        await ctx.send("❌ You don't have permission to stop the bot.")

# ----- Basic Test Command -----
@bot.command()
async def hello(ctx):
    await ctx.send("Hello, world! 👋")

# ----- Gemini API Integration -----
def get_gemini_response(user_input):
    try:
        response = model.generate_content(user_input)
        return response.text
    except google.api_core.exceptions.ResourceExhausted:
        return "🚫 I'm out of quota! Please try again later."
    except Exception as e:
        return f"⚠️ Oops! Something went wrong: {str(e)}"

# Function to safely send long responses (splitting into chunks if needed)
async def send_response(channel, response):
    for i in range(0, len(response), 2000):
        await channel.send(response[i:i+2000])

# ----- Chat Command (with natural language reminder support) -----
@bot.command()
async def chat(ctx, *, message: str):
    """
    If the message starts with "set reminder", the bot interprets it as a natural language reminder command.
    It first checks for an explicit "on ... at ..." pattern to extract day, date, and time.
    Otherwise, it uses search_dates with a proper RELATIVE_BASE to determine the intended reminder time.
    If no valid datetime is found, it returns an error.
    Otherwise, it sets the reminder.
    If the message does not start with "set reminder", it sends the message to the Gemini API.
    """
    trigger = "set reminder"
    if message.lower().startswith(trigger):
        # Remove trigger phrase
        reminder_text = message[len(trigger):].strip()
        user_tz = user_timezones.get(ctx.author.id, "UTC")
        relative_base = datetime.now(pytz.timezone(user_tz))
        reminder_datetime = None

        # Check for an explicit "on ... at ..." pattern.
        explicit_match = re.search(r'on\s+(.+?)\s+at\s+(.+)', reminder_text, re.IGNORECASE)
        if explicit_match:
            date_part = explicit_match.group(1)
            time_part = explicit_match.group(2)
            combined = f"{date_part} {time_part}"
            reminder_datetime = dateparser.parse(
                combined,
                settings={
                    'TIMEZONE': user_tz,
                    'RETURN_AS_TIMEZONE_AWARE': True,
                    'PREFER_DATES_FROM': 'future',
                    'RELATIVE_BASE': relative_base
                }
            )
        # If no explicit pattern, use search_dates.
        if reminder_datetime is None:
            results = search_dates(
                reminder_text,
                settings={
                    'TIMEZONE': user_tz,
                    'RETURN_AS_TIMEZONE_AWARE': True,
                    'PREFER_DATES_FROM': 'future',
                    'RELATIVE_BASE': relative_base
                }
            )
            if results:
                reminder_datetime = results[-1][1]
        # Fallback: manual extraction for "in X minutes/hours"
        if reminder_datetime is None:
            match = re.search(r'in\s+(\d+)\s+minutes?', reminder_text, re.IGNORECASE)
            if match:
                minutes = int(match.group(1))
                reminder_datetime = datetime.now(pytz.timezone(user_tz)) + timedelta(minutes=minutes)
            else:
                match = re.search(r'in\s+(\d+)\s+hours?', reminder_text, re.IGNORECASE)
                if match:
                    hours = int(match.group(1))
                    reminder_datetime = datetime.now(pytz.timezone(user_tz)) + timedelta(hours=hours)
        if reminder_datetime is None:
            await ctx.send("⚠️ Could not determine a valid time from your reminder text. Please include a clear time reference (e.g., 'in 20 minutes', 'at 3pm', or 'tomorrow at 3:50 pm').")
            return
        user_id = ctx.author.id
        if user_id not in reminders:
            reminders[user_id] = []
        reminders[user_id].append((reminder_datetime, reminder_text))
        await ctx.send(f"⏰ Reminder set for {reminder_datetime.strftime('%Y-%m-%d %H:%M %Z')} with message: {reminder_text}")
    else:
        ai_reply = get_gemini_response(message)
        await send_response(ctx.channel, ai_reply)

# ----- Mode Command (AI vs Normal) -----
@bot.command()
async def mode(ctx, mode_type: str):
    if mode_type.lower() == "ai":
        user_ai_mode[ctx.author.id] = True
        await ctx.send("🤖 AI Mode Enabled! The bot will respond to all messages.")
    elif mode_type.lower() == "normal":
        user_ai_mode[ctx.author.id] = False
        await ctx.send("💬 Normal Mode Enabled! The bot will only respond to commands.")
    else:
        await ctx.send("⚠️ Invalid mode. Use `!mode ai` or `!mode normal`.")

# ----- Event Listeners -----
@bot.event
async def on_ready():
    print(f'✅ Logged in as {bot.user}')
    check_reminders.start()

@bot.event
async def on_member_join(member):
    # Replace with your actual welcome channel ID
    welcome_channel_id = 123456789012345678  
    channel = member.guild.get_channel(welcome_channel_id)
    if channel:
        await channel.send(f"Welcome {member.mention} to the server!")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    if message.content.startswith("!"):
        await bot.process_commands(message)
        return
    if user_ai_mode.get(message.author.id, False):
        ai_reply = get_gemini_response(message.content)
        await send_response(message.channel, ai_reply)

# ----- Poll Command -----
@bot.command()
async def poll(ctx, question: str, *options: str):
    if len(options) < 2 or len(options) > 10:
        await ctx.send("Poll must have between 2 and 10 options!")
        return
    embed = discord.Embed(title="📊 Poll", description=question, color=discord.Color.blue())
    number_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    poll_text = ""
    for i, option in enumerate(options):
        poll_text += f"{number_emojis[i]} {option}\n"
    embed.add_field(name="Options", value=poll_text, inline=False)
    embed.set_footer(text="React with an emoji to vote!")
    poll_message = await ctx.send(embed=embed)
    for i in range(len(options)):
        await poll_message.add_reaction(number_emojis[i])

# ----- Time Zone Command -----
@bot.command()
async def settimezone(ctx, tz: str):
    try:
        pytz.timezone(tz)
        user_timezones[ctx.author.id] = tz
        await ctx.send(f"🌍 Timezone set to `{tz}`.")
    except pytz.UnknownTimeZoneError:
        await ctx.send("⚠️ Invalid timezone. Please use a valid timezone name (e.g., `UTC`, `America/New_York`, `Asia/Kolkata`).")

# ----- Strict Reminder Command -----
@bot.command()
async def remind(ctx, time: str, *, message: str):
    """
    Set a reminder using a strict format: !remind HH:MM <message>
    If the time has passed today, the reminder is set for the next day.
    """
    user_id = ctx.author.id
    user_tz = user_timezones.get(user_id, "UTC")
    try:
        user_timezone = pytz.timezone(user_tz)
        reminder_time = datetime.strptime(time, "%H:%M").time()
        now = datetime.now(user_timezone)
        reminder_datetime = user_timezone.localize(datetime.combine(now.date(), reminder_time))
        if reminder_datetime < now:
            reminder_datetime += timedelta(days=1)
        if user_id not in reminders:
            reminders[user_id] = []
        reminders[user_id].append((reminder_datetime, message))
        await ctx.send(f"⏰ Reminder set for {reminder_datetime.strftime('%Y-%m-%d %H:%M %Z')} - {message}")
    except ValueError:
        await ctx.send("⚠️ Invalid time format! Use HH:MM (24-hour format).")

# ----- Interactive Delete Reminder Command -----
@bot.command()
async def delreminder(ctx, index: int = None):
    """
    Deletes a reminder. If an index is provided, deletes that specific reminder.
    If no index is provided, lists all reminders and asks the user to choose one.
    """
    user_id = ctx.author.id
    if user_id not in reminders or not reminders[user_id]:
        await ctx.send("⚠️ You don't have any active reminders.")
        return

    user_reminders = reminders[user_id]
    # If index is provided and valid, delete that reminder.
    if index is not None:
        if 1 <= index <= len(user_reminders):
            removed = user_reminders.pop(index - 1)
            await ctx.send(f"🗑️ Deleted reminder set for {removed[0].strftime('%Y-%m-%d %H:%M %Z')} - {removed[1]}")
        else:
            await ctx.send("⚠️ Invalid index. Please provide a valid reminder number.")
        return

    # Otherwise, list reminders and ask for a choice.
    response = "Please reply with the number of the reminder you want to delete:\n"
    for i, (rem_time, msg) in enumerate(user_reminders, start=1):
        response += f"{i}. {rem_time.strftime('%Y-%m-%d %H:%M %Z')} - {msg}\n"
    await ctx.send(response)

    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    try:
        reply = await bot.wait_for("message", timeout=30.0, check=check)
        choice = int(reply.content)
        if 1 <= choice <= len(user_reminders):
            removed = user_reminders.pop(choice - 1)
            await ctx.send(f"🗑️ Deleted reminder set for {removed[0].strftime('%Y-%m-%d %H:%M %Z')} - {removed[1]}")
        else:
            await ctx.send("⚠️ Invalid number. No reminder deleted.")
    except asyncio.TimeoutError:
        await ctx.send("⏰ Timeout: No response received. No reminder deleted.")
    except ValueError:
        await ctx.send("⚠️ Please enter a valid number.")

# ----- Background Task: Check Reminders -----
@tasks.loop(seconds=10)
async def check_reminders():
    now = datetime.now(pytz.utc)
    for user_id, reminder_list in list(reminders.items()):
        remaining = []
        for reminder_time, message in reminder_list:
            if now >= reminder_time:
                user = await bot.fetch_user(user_id)
                if user:
                    await user.send(f"🔔 Reminder: {message}")
            else:
                remaining.append((reminder_time, message))
        reminders[user_id] = remaining

# ----- Run the Bot -----
bot.run(DISCORD_BOT_TOKEN)
