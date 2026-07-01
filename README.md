# Magna Bot 🤖

A Discord bot built with Python and discord.py that combines fun interactions with utility features. The bot includes AI chat capabilities through Google's Gemini API, Reddit meme fetching, birthday tracking, and member counting functionality.

## Features 🌟

- **AI Chat**: Chat with Magna Shanoa using Google's Gemini AI (`%ask`)
- **Reddit Memes**: Fetch random memes from popular subreddits (`%meme`)
- **Birthday Tracking**: Manage and celebrate member birthdays (`%birthday`)
- **Member Counter**: Automatic server member counting
- **Simple Commands**: Basic interaction commands (`%hello`, `%ping`)
- **Music Player**: Advanced music player with multiple features:
  - Play YouTube URLs (`%play [url]`)
  - Search and play songs (`%play [search term]`)
  - Play Spotify tracks (`%play [spotify url]`)
  - Queue management (`%queue`)
  - Playback controls (`%pause`, `%stop`, `%skip`)
  - Voice channel management (`%leave`)

## Music Commands 🎵

- `%play [url/search/spotify]`: Play music from YouTube URL, Spotify URL, or search term
- `%pause`: Pause/Resume the current song
- `%stop`: Stop playing and clear the queue
- `%skip`: Skip to the next song
- `%queue`: Show the current queue
- `%leave`: Leave the voice channel

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

3. Install system dependencies:
```bash
# For Debian/Ubuntu
sudo apt-get update && sudo apt-get install -y ffmpeg

# For Arch Linux
sudo pacman -S ffmpeg
```

4. Run the bot:
```bash
python main.py
```

## Imagem no Docker Hub

Cada push na branch `main` publica automaticamente uma imagem ARM64 em
`docker.io/gabborges/magna-bot:latest` por meio de um runner ARM64 nativo do
GitHub Actions. O servidor não precisa receber o código-fonte nem fazer o build.

A imagem inclui FFmpeg para reprodução de áudio. O recurso de renderização
LaTeX foi removido para evitar centenas de megabytes de dependências adicionais.

No servidor, crie um diretório somente para configuração e dados:

```bash
mkdir -p ~/magna-bot/data
cd ~/magna-bot
nano .env
```

Preencha o `.env` usando as variáveis descritas em `.env.example`. Depois execute:

```bash
podman pull docker.io/gabborges/magna-bot:latest
podman run -d \
  --name magna-bot \
  --restart=unless-stopped \
  --env-file .env \
  -v "$(pwd)/data:/app/data:U" \
  docker.io/gabborges/magna-bot:latest
```

O sufixo `:U` ajusta a posse do diretório persistente para o usuário não-root
da imagem. Em um sistema com SELinux habilitado, use `:U,Z`.

Comandos operacionais:

```bash
podman logs -f magna-bot
podman stop magna-bot
podman start magna-bot

# Atualização depois que uma nova imagem for publicada:
podman pull docker.io/gabborges/magna-bot:latest
podman rm -f magna-bot
# Execute novamente o comando "podman run" acima.
```

Se o repositório do Docker Hub estiver privado, autentique o Podman antes do
`pull` usando um access token do Docker Hub:

```bash
printf '%s' "$DOCKERHUB_TOKEN" | podman login docker.io -u gabborges --password-stdin
```

O `.env` e os cookies não são copiados para a imagem. `birthdays.json`,
`birthday_channels.json` e `membercount.json` ficam no diretório `data`, portanto
continuam existindo quando o container for recriado.
