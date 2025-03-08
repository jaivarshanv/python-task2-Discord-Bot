import discord
import os
import sys
import asyncio
import re
import yt_dlp
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

# ----------------- Configuration -----------------
DISCORD_BOT_TOKEN = ""
GEMINI_API_KEY = ""


# Optionally, specify the full path to the FFmpeg executable if not in PATH.
ffmpeg_executable = os.getenv("FFMPEG_PATH", "ffmpeg")

# Configure the Gemini API
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-pro-latest")

# Set up bot with necessary intents (including voice)
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ----------------- Global Variables -----------------
conversation_history = {}  # {channel_id: list of strings}
HISTORY_LIMIT = 6

user_timezones = {}     # {user_id: timezone_str}
reminders = {}          # {user_id: list of tuples (reminder_datetime, message)}
user_ai_mode = {}       # {user_id: bool}

# For music, we store tuples (song_url, song_title)
music_queues = {}       # {guild_id: list of tuples (song_url, song_title)}
now_playing = {}        # {guild_id: tuple (song_url, song_title)}

# ----------------- Helper Functions -----------------
def get_gemini_response(prompt):
    try:
        response = model.generate_content(prompt)
        return response.text
    except google.api_core.exceptions.ResourceExhausted:
        return "🚫 I'm out of quota! Please try again later."
    except Exception as e:
        return f"⚠️ Oops! Something went wrong: {str(e)}"

async def send_response(channel, response):
    for i in range(0, len(response), 2000):
        await channel.send(response[i:i+2000])

def update_history(channel_id, role, content):
    if channel_id not in conversation_history:
        conversation_history[channel_id] = []
    conversation_history[channel_id].append(f"{role}: {content}")
    if len(conversation_history[channel_id]) > HISTORY_LIMIT:
        conversation_history[channel_id] = conversation_history[channel_id][-HISTORY_LIMIT:]

def search_youtube(query):
    ydl_opts = {
        'format': 'bestaudio/best',
        'noplaylist': True,
        'quiet': True
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch:{query}", download=False)
            if 'entries' in info and info['entries']:
                video = info['entries'][0]
            else:
                video = info
            title = video.get('title', 'Unknown Title')
            if 'formats' in video:
                best_format = max(video['formats'], key=lambda f: f.get('abr') or 0)
                url = best_format['url']
            else:
                url = video.get('url')
            return (url, title)
    except Exception as e:
        print(f"Error searching YouTube: {e}")
        return (None, None)

# ----------------- On Ready Event -----------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    check_reminders.start()

# ----------------- Admin Commands -----------------
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

# ----------------- Basic Commands -----------------
@bot.command()
async def hello(ctx):
    await ctx.send("Hello, world! 👋")

# ----------------- Music Commands -----------------
@bot.command()
async def play(ctx, *, query: str):
    if not ctx.author.voice:
        await ctx.send("You must be in a voice channel to play music.")
        return

    voice_channel = ctx.author.voice.channel
    guild_id = ctx.guild.id
    voice_client = ctx.guild.voice_client

    if voice_client is None:
        voice_client = await voice_channel.connect()
        await ctx.send("Connected to the voice channel.")
    elif voice_client.channel != voice_channel:
        await voice_client.move_to(voice_channel)
        await ctx.send("Moved to your voice channel.")

    if not re.match(r'https?://', query):
        await ctx.send("Searching YouTube for your song...")
        url, title = search_youtube(query)
        if url is None:
            await ctx.send("⚠️ Could not find a matching video on YouTube.")
            return
    else:
        url = query
        title = query

    if guild_id not in music_queues:
        music_queues[guild_id] = []
    music_queues[guild_id].append((url, title))
    embed = discord.Embed(description=f"Added to queue: [{title}]({url})", color=discord.Color.green())
    await ctx.send(embed=embed)

    # If voice client is paused, resume; if not playing, start next song.
    if voice_client.is_paused():
        voice_client.resume()
        await ctx.send("Resumed playback.")
    elif not voice_client.is_playing():
        await play_next_song(ctx, voice_client)

async def play_next_song(ctx, voice_client):
    guild_id = ctx.guild.id
    if guild_id in music_queues and music_queues[guild_id]:
        next_song = music_queues[guild_id].pop(0)
        now_playing[guild_id] = next_song
        ffmpeg_options = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 10',
            'options': '-vn'
        }
        try:
            source = discord.PCMVolumeTransformer(
                discord.FFmpegPCMAudio(next_song[0], executable=ffmpeg_executable, **ffmpeg_options)
            )
        except Exception as e:
            await ctx.send(f"Error creating audio source: {e}")
            return

        def after_playing(error):
            if error:
                print(f"Error playing audio: {error}")
            fut = asyncio.run_coroutine_threadsafe(play_next_song(ctx, voice_client), bot.loop)
            try:
                fut.result()
            except Exception as e:
                print(f"Error in after callback: {e}")

        voice_client.play(source, after=after_playing)
        embed = discord.Embed(description=f"Now playing: [{next_song[1]}]({next_song[0]})", color=discord.Color.green())
        await ctx.send(embed=embed)
    else:
        now_playing[guild_id] = None
        await ctx.send("The music queue is empty.")

@bot.command()
async def skip(ctx):
    voice_client = ctx.guild.voice_client
    if voice_client and voice_client.is_playing():
        voice_client.stop()
        await ctx.send("Skipped the current song.")
    else:
        await ctx.send("No song is currently playing.")

@bot.command()
async def pause(ctx):
    voice_client = ctx.guild.voice_client
    if voice_client and voice_client.is_playing():
        voice_client.pause()
        await ctx.send("Paused the current song.")
    else:
        await ctx.send("No song is currently playing.")

@bot.command()
async def resume(ctx):
    voice_client = ctx.guild.voice_client
    if voice_client and voice_client.is_paused():
        voice_client.resume()
        await ctx.send("Resumed the current song.")
    else:
        await ctx.send("The song is not paused or no song is playing.")

@bot.command()
async def queue(ctx):
    guild_id = ctx.guild.id
    embed = discord.Embed(title="Music Queue", color=discord.Color.blue())
    voice_client = ctx.guild.voice_client
    if voice_client and voice_client.is_playing() and guild_id in now_playing and now_playing[guild_id]:
        current = now_playing[guild_id]
        embed.add_field(name="Now Playing", value=f"[{current[1]}]({current[0]})", inline=False)
    if guild_id in music_queues and music_queues[guild_id]:
        upcoming = ""
        for i, song in enumerate(music_queues[guild_id]):
            upcoming += f"{i+1}. [{song[1]}]({song[0]})\n"
        if len(upcoming) > 1024:
            upcoming = upcoming[:1021] + "..."
        embed.add_field(name="Upcoming Songs", value=upcoming, inline=False)
    if not embed.fields:
        embed.description = "The music queue is empty."
    await ctx.send(embed=embed)

@bot.command()
async def nowplaying(ctx):
    guild_id = ctx.guild.id
    if guild_id in now_playing and now_playing[guild_id]:
        song = now_playing[guild_id]
        await ctx.send(f"Now playing: [{song[1]}]({song[0]})")
    else:
        await ctx.send("No song is currently playing.")

@bot.command(name="skip_to")
async def skip_to(ctx, index: int):
    guild_id = ctx.guild.id
    if guild_id not in music_queues or index < 1 or index > len(music_queues[guild_id]):
        await ctx.send("Invalid index. Please provide a valid song number from the queue.")
        return
    voice_client = ctx.guild.voice_client
    if voice_client and voice_client.is_playing():
        voice_client.stop()
    # Remove songs until we reach the specified one
    for _ in range(index - 1):
        music_queues[guild_id].pop(0)
    await ctx.send(f"Skipping to song number {index} in the queue...")
    await play_next_song(ctx, voice_client)

# ----------------- Conversation Context & Task Processing -----------------
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if message.content.startswith("!"):
        await bot.process_commands(message)
        return

    content = message.content.strip()
    channel_id = message.channel.id

    # Natural Language Poll Creation
    if content.lower().startswith("create poll:"):
        pattern = re.compile(r"create poll:\s*(.*?)\s*options:\s*(.*)", re.IGNORECASE)
        match = pattern.search(content)
        if match:
            question = match.group(1).strip()
            options_str = match.group(2).strip()
            options = [opt.strip() for opt in re.split(r",|;", options_str) if opt.strip()]
            if len(options) < 2:
                await message.channel.send("⚠️ Please provide at least two options separated by commas or semicolons.")
                return
            embed = discord.Embed(title="📊 Poll", description=question, color=discord.Color.blue())
            number_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
            poll_text = ""
            for i, option in enumerate(options):
                poll_text += f"{number_emojis[i]} {option}\n"
            embed.add_field(name="Options", value=poll_text, inline=False)
            embed.set_footer(text="React with an emoji to vote!")
            poll_message = await message.channel.send(embed=embed)
            for i in range(len(options)):
                await poll_message.add_reaction(number_emojis[i])
            update_history(channel_id, "User", content)
            update_history(channel_id, "Bot", f"Created poll: {question}")
            return

    # Natural Language Reminder Processing
    elif content.lower().startswith("set reminder"):
        reminder_text = content[len("set reminder"):].strip()
        user_tz = user_timezones.get(message.author.id, "UTC")
        relative_base = datetime.now(pytz.timezone(user_tz))
        reminder_datetime = None

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
        if reminder_datetime is None:
            match_sec = re.search(r'in\s+(\d+)\s+seconds?', reminder_text, re.IGNORECASE)
            if match_sec:
                seconds = int(match_sec.group(1))
                reminder_datetime = datetime.now(pytz.timezone(user_tz)) + timedelta(seconds=seconds)
        if reminder_datetime is None:
            match_min = re.search(r'in\s+(\d+)\s+minutes?', reminder_text, re.IGNORECASE)
            if match_min:
                minutes = int(match_min.group(1))
                reminder_datetime = datetime.now(pytz.timezone(user_tz)) + timedelta(minutes=minutes)
            else:
                match_hr = re.search(r'in\s+(\d+)\s+hours?', reminder_text, re.IGNORECASE)
                if match_hr:
                    hours = int(match_hr.group(1))
                    reminder_datetime = datetime.now(pytz.timezone(user_tz)) + timedelta(hours=hours)
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
        if reminder_datetime is None:
            await message.channel.send("⚠️ Could not determine a valid time from your reminder text. Please include a clear time reference (e.g., 'in 20 minutes', 'at 3pm', 'in 15 seconds', or 'tomorrow at 3:50 pm').")
            return
        user_id = message.author.id
        if user_id not in reminders:
            reminders[user_id] = []
        formatted_time = reminder_datetime.strftime('%Y-%m-%d %H:%M:%S %Z')
        reminders[user_id].append((reminder_datetime, reminder_text))
        await message.channel.send(f"⏰ Reminder set for {formatted_time} with message: {reminder_text}")
        update_history(channel_id, "User", content)
        update_history(channel_id, "Bot", f"Set reminder for {formatted_time}")
        return

    # Otherwise, process as regular chat with context
    else:
        history = conversation_history.get(channel_id, [])
        prompt = "\n".join(history[-HISTORY_LIMIT:]) + f"\nUser: {content}\nBot:"
        lc_content = content.lower()
        if "elaborate" not in lc_content and "summarise" not in lc_content:
            prompt = "Respond briefly: " + prompt
        ai_reply = get_gemini_response(prompt)
        await send_response(message.channel, ai_reply)
        update_history(channel_id, "User", content)
        update_history(channel_id, "Bot", ai_reply)

# ----------------- Strict Reminder Command -----------------
@bot.command()
async def remind(ctx, time: str, *, message: str):
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
        formatted_time = reminder_datetime.strftime('%Y-%m-%d %H:%M:%S %Z')
        await ctx.send(f"⏰ Reminder set for {formatted_time} - {message}")
    except ValueError:
        await ctx.send("⚠️ Invalid time format! Use HH:MM (24-hour format).")

# ----------------- Interactive Delete Reminder Command -----------------
@bot.command()
async def delreminder(ctx, index: int = None):
    user_id = ctx.author.id
    if user_id not in reminders or not reminders[user_id]:
        await ctx.send("⚠️ You don't have any active reminders.")
        return

    user_reminders = reminders[user_id]
    if index is not None:
        if 1 <= index <= len(user_reminders):
            removed = user_reminders.pop(index - 1)
            formatted_time = removed[0].strftime('%Y-%m-%d %H:%M:%S %Z')
            await ctx.send(f"🗑️ Deleted reminder set for {formatted_time} - {removed[1]}")
        else:
            await ctx.send("⚠️ Invalid index. Please provide a valid reminder number.")
        return

    response = "Please reply with the number of the reminder you want to delete:\n"
    for i, (rem_time, msg) in enumerate(user_reminders, start=1):
        response += f"{i}. {rem_time.strftime('%Y-%m-%d %H:%M:%S %Z')} - {msg}\n"
    await ctx.send(response)

    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    try:
        reply = await bot.wait_for("message", timeout=30.0, check=check)
        choice = int(reply.content)
        if 1 <= choice <= len(user_reminders):
            removed = user_reminders.pop(choice - 1)
            formatted_time = removed[0].strftime('%Y-%m-%d %H:%M:%S %Z')
            await ctx.send(f"🗑️ Deleted reminder set for {formatted_time} - {removed[1]}")
        else:
            await ctx.send("⚠️ Invalid number. No reminder deleted.")
    except asyncio.TimeoutError:
        await ctx.send("⏰ Timeout: No response received. No reminder deleted.")
    except ValueError:
        await ctx.send("⚠️ Please enter a valid number.")

# ----------------- Time Zone Command -----------------
@bot.command()
async def settimezone(ctx, tz: str):
    try:
        pytz.timezone(tz)
        user_timezones[ctx.author.id] = tz
        await ctx.send(f"🌍 Timezone set to `{tz}`.")
    except pytz.UnknownTimeZoneError:
        await ctx.send("⚠️ Invalid timezone. Please use a valid timezone name (e.g., `UTC`, `America/New_York`, `Asia/Kolkata`).")

# ----------------- Background Task: Check Reminders -----------------
@tasks.loop(seconds=1)
async def check_reminders():
    now = datetime.now(pytz.utc)
    for user_id, reminder_list in list(reminders.items()):
        remaining = []
        for reminder_time, message in reminder_list:
            if now >= reminder_time:
                user = await bot.fetch_user(user_id)
                if user:
                    try:
                        await user.send(f"🔔 Reminder: {message}")
                    except Exception as e:
                        print(f"Failed to send reminder to {user_id}: {e}")
            else:
                remaining.append((reminder_time, message))
        reminders[user_id] = remaining

# ----------------- Run the Bot -----------------
bot.run(DISCORD_BOT_TOKEN)
