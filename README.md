# Magna Bot 🤖

A Discord bot built with Python and discord.py that combines fun interactions with utility features. The bot includes AI chat capabilities through Google's Gemini API, Reddit meme fetching, birthday tracking, and member counting functionality.

## Features 🌟

- **AI Chat**: Chat with Magna Shanoa using Google's Gemini AI (`%ask`)
- **Reddit Memes**: Fetch random memes from popular subreddits (`%meme`)
- **Birthday Tracking**: Manage and celebrate member birthdays (`%birthday`)
- **Member Counter**: Automatic server member counting
- **Simple Commands**: Basic interaction commands (`%hello`, `%ping`)
- **Music Player**: Play YouTube songs in voice channels (`%play`, `%stop`, `%skip`, `%queue`)

## Setup 🚀

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Set up environment variables in `.env`:
- `TOKEN` - Discord Bot Token
- `GEMINI_API_KEY` - Google Gemini API Key
- `REDDIT_CLIENT_ID` - Reddit API Client ID
- `REDDIT_CLIENT_SECRET` - Reddit API Client Secret
- `REDDIT_USER_AGENT` - Reddit API User Agent

3. Run the bot:
```bash
python main.py
```

## Docker 🐳
```bash
docker build -t magna-bot .
docker run -d --env-file .env magna-bot
```
