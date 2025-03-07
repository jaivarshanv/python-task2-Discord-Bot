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
DISCORD_BOT_TOKEN = ""
GEMINI_API_KEY = ""

# Configure the Gemini API via the google.generativeai library
genai.configure(api_key=GEMINI_API_KEY)
# Initialize the model (adjust the model name if needed)
model = genai.GenerativeModel("gemini-1.5-pro-latest")

# Set up the Discord bot with required intents
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Global conversation history per channel (keep last N messages)
conversation_history = {}  # { channel_id: list of messages }
HISTORY_LIMIT = 6  # Adjust number of messages to include in context

# Dictionaries for storing user time zones and reminders
user_timezones = {}     # { user_id: timezone_str }
reminders = {}          # { user_id: list of tuples (reminder_datetime, message) }
# For auto-AI mode per user (optional)
user_ai_mode = {}       # { user_id: bool }

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

# ----- Active Chat Commands -----
@bot.command()
async def hello(ctx):
    await ctx.send("Hello, world! 👋")

# ----- Gemini API Integration -----
def get_gemini_response(prompt):
    try:
        response = model.generate_content(prompt)
        return response.text
    except google.api_core.exceptions.ResourceExhausted:
        return "🚫 I'm out of quota! Please try again later."
    except Exception as e:
        return f"⚠️ Oops! Something went wrong: {str(e)}"

# Function to safely send long responses
async def send_response(channel, response):
    for i in range(0, len(response), 2000):
        await channel.send(response[i:i+2000])

# Helper to update conversation history (store up to HISTORY_LIMIT messages)
def update_history(channel_id, role, content):
    if channel_id not in conversation_history:
        conversation_history[channel_id] = []
    conversation_history[channel_id].append(f"{role}: {content}")
    if len(conversation_history[channel_id]) > HISTORY_LIMIT:
        conversation_history[channel_id] = conversation_history[channel_id][-HISTORY_LIMIT:]

# ----- on_message: Context-Aware Chat and Natural Language Reminders -----
@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    # Process commands if message starts with the command prefix
    if message.content.startswith("!"):
        await bot.process_commands(message)
        return

    channel_id = message.channel.id

    # Check if this message is a natural language reminder command
    if message.content.lower().startswith("set reminder"):
        reminder_text = message.content[len("set reminder"):].strip()
        user_tz = user_timezones.get(message.author.id, "UTC")
        relative_base = datetime.now(pytz.timezone(user_tz))
        reminder_datetime = None

        # Check for an explicit "on ... at ..." pattern
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
        # Otherwise, use search_dates over the text
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
        # Fallback: manual extraction
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
            await message.channel.send("⚠️ Could not determine a valid time from your reminder text. Please include a clear time reference (e.g., 'in 20 minutes', 'at 3pm', or 'tomorrow at 3:50 pm').")
            return

        user_id = message.author.id
        if user_id not in reminders:
            reminders[user_id] = []
        reminders[user_id].append((reminder_datetime, reminder_text))
        await message.channel.send(f"⏰ Reminder set for {reminder_datetime.strftime('%Y-%m-%d %H:%M %Z')} with message: {reminder_text}")
        # Optionally update conversation history as context
        update_history(channel_id, "User", message.content)
        update_history(channel_id, "Bot", f"Set reminder for {reminder_datetime.strftime('%Y-%m-%d %H:%M %Z')}")
    else:
        # Build context prompt from conversation history (if any)
        history = conversation_history.get(channel_id, [])
        prompt = "\n".join(history[-HISTORY_LIMIT:]) + f"\nUser: {message.content}\nBot:"
        ai_reply = get_gemini_response(prompt)
        await send_response(message.channel, ai_reply)
        # Update conversation history with both user and bot messages
        update_history(channel_id, "User", message.content)
        update_history(channel_id, "Bot", ai_reply)

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
    Delete a reminder. If an index is provided, delete that specific reminder.
    If no index is provided, list all reminders and let the user choose which one to delete.
    """
    user_id = ctx.author.id
    if user_id not in reminders or not reminders[user_id]:
        await ctx.send("⚠️ You don't have any active reminders.")
        return

    user_reminders = reminders[user_id]
    if index is not None:
        if 1 <= index <= len(user_reminders):
            removed = user_reminders.pop(index - 1)
            await ctx.send(f"🗑️ Deleted reminder set for {removed[0].strftime('%Y-%m-%d %H:%M %Z')} - {removed[1]}")
        else:
            await ctx.send("⚠️ Invalid index. Please provide a valid reminder number.")
        return

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
