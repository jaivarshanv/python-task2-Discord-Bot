# Discord Music & Reminder Bot

This project is a multifunctional Discord bot that offers:

- **Music Playback**: Play audio from YouTube in voice channels.  
  Commands: `!play`, `!pause`, `!resume`, `!skip`, `!skip_to`, `!queue`, `!nowplaying`
- **Reminders**: Set natural language reminders (supports seconds, minutes, and hours) that send a direct message (DM) at the specified time.  
  Commands: Natural language (e.g., `set reminder to call Ram in 15 seconds`), `!remind`, `!delreminder`
- **Polls & Context-Aware Chat**: Create polls and have the bot respond to chat messages with context using the Gemini API create poll: Question? options: o1,o2,etc.
- **Timezone Support**: Users can set their preferred timezone with `!settimezone`.

## Features

### Music Playback
- **!play `<query>`**  
  - Joins your voice channel.
  - Searches YouTube (if query is not a URL) and adds the song (with a hidden hyperlink for the title) to the queue.
- **!pause / !resume**  
  - Pauses and resumes playback.
- **!skip**  
  - Skips the current song.
- **!skip_to `<index>`**  
  - Skips directly to a song in the queue by its number.
- **!queue**  
  - Displays the current "Now Playing" song and the upcoming songs.
- **!nowplaying**  
  - Displays details of the currently playing song.

### Reminders
- **Natural Language Reminder**  
  - Type something like:  
    `set reminder to call Ram in 15 seconds`
- **!remind `<HH:MM>` `<message>`**  
  - Set a reminder using strict time format.
- **!delreminder**  
  - Delete a reminder interactively.
- **Timezone**  
  - Use `!settimezone <timezone>` (e.g., `!settimezone Asia/Kolkata` for IST) to set your preferred timezone.

### Polls & Chat
- **Poll Creation**  
  - Type:  
    `create poll: What is your favorite color? options: Red, Blue, Green`
  - The bot creates an embed poll with reaction options.
- **Context-Aware Chat**  
  - Non-command messages are processed for context-based responses via the Gemini API.

## Dependencies

- [discord.py](https://discordpy.readthedocs.io/) (Python Discord API wrapper)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) (Extracts audio URLs from YouTube)
- [FFmpeg](https://ffmpeg.org/) (Streams audio; must be installed and in your system PATH)
- [google-generativeai](https://pypi.org/project/google-generativeai/) (Gemini API integration)
- [dateparser](https://pypi.org/project/dateparser/) (Parses natural language date/time expressions)
- [pytz](https://pypi.org/project/pytz/) (Timezone support)

## Installation

1. **Clone the Repository**
   ```bash
   git clone https://github.com/yourusername/discord-music-reminder-bot.git
   cd discord-music-reminder-bot
