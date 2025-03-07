import discord
import os
import sys
import asyncio
import google.generativeai as genai
import google.api_core.exceptions
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import pytz
import dateparser
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

# Dictionaries for storing user time zones, reminders, and AI mode preferences
user_timezones = {}     # { user_id: timezone_str }
reminders = {}          # { user_id: (reminder_datetime, message) }
user_ai_mode = {}       # { user_id: bool }  True means auto-AI response enabled

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

# ----- Chat Command -----
@bot.command()
async def chat(ctx, *, message: str):
    """Chat with the Gemini API."""
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
    # Process commands if message starts with the command prefix
    if message.content.startswith("!"):
        await bot.process_commands(message)
        return
    # Auto-respond with AI if user AI mode is enabled
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

# ----- Reminder Command (Strict Format) -----
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
        reminders[user_id] = (reminder_datetime, message)
        await ctx.send(f"⏰ Reminder set for {reminder_datetime.strftime('%Y-%m-%d %H:%M %Z')} - {message}")
    except ValueError:
        await ctx.send("⚠️ Invalid time format! Use HH:MM (24-hour format).")

# ----- Natural Language Reminder Command -----
@bot.command()
async def remindme(ctx, *, text: str):
    """
    Set a reminder using natural language.
    Example: !remindme remind me to take a break in 10 minutes
    The bot will try to parse the time from your message.
    """
    # Use dateparser to parse the date/time from the text
    reminder_datetime = dateparser.parse(
        text,
        settings={'TIMEZONE': 'UTC', 'RETURN_AS_TIMEZONE_AWARE': True, 'PREFER_DATES_FROM': 'future'}
    )
    if not reminder_datetime:
        await ctx.send("⚠️ Could not determine the time from your message. Please include a clear time reference.")
        return
    user_id = ctx.author.id
    # Here we use the full natural language text as the reminder message
    reminders[user_id] = (reminder_datetime, text)
    await ctx.send(f"⏰ Reminder set for {reminder_datetime.strftime('%Y-%m-%d %H:%M %Z')} - {text}")

# ----- Delete Reminder Command -----
@bot.command()
async def delreminder(ctx):
    user_id = ctx.author.id
    if user_id in reminders:
        del reminders[user_id]
        await ctx.send("🗑️ Your reminder has been deleted.")
    else:
        await ctx.send("⚠️ You don't have any active reminders.")

# ----- Background Task: Check Reminders -----
@tasks.loop(seconds=10)
async def check_reminders():
    now = datetime.now(pytz.utc)
    to_remove = []
    for user_id, (reminder_time, message) in reminders.items():
        if now >= reminder_time:
            user = await bot.fetch_user(user_id)
            if user:
                await user.send(f"🔔 Reminder: {message}")
            to_remove.append(user_id)
    for user_id in to_remove:
        del reminders[user_id]

# ----- Run the Bot -----
bot.run(DISCORD_BOT_TOKEN)
