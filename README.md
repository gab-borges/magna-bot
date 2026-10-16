# Magna Bot 🤖

A Discord bot built with Python and discord.py that combines fun interactions with utility features. The bot includes AI chat capabilities through Google's Gemini API, Reddit meme fetching, birthday tracking, and member counting functionality.

## Features 🌟

- **AI Chat**: Chat with Magna Shanoa using Google's Gemini AI (`%ask`)
- **Reddit Memes**: Fetch random memes from popular subreddits (`%meme`)
- **Birthday Tracking**: Manage and celebrate member birthdays (`%birthday`)
- **Member Counter**: Automatic server member counting
- **Simple Commands**: Basic interaction commands (`%hello`, `%ping`)
- **TimeGuessr**: Guess where and when photos were taken (`%tg`, see below)
- **Music Player**: Advanced music player with multiple features:
  - Play YouTube URLs (`%play [url]`)
  - Search and play songs (`%play [search term]`)
  - Play Spotify tracks, albums and playlists (`%play [spotify url]`)
  - Queue management (`%queue`)
  - Playback controls (`%pause`, `%stop`, `%skip`)
  - Voice channel management (`%leave`)

## Slash Commands ⚡

Every command works both with the `%` prefix and as a Discord slash command:
`%play never gonna give you up` and `/play` do the same thing. Slash commands
show a description of each argument while typing, and some suggest values:

- `/play` suggests songs from YouTube Music as you type; picking one plays
  that exact song. Typing a link or any other text works as with `%play`.
- `/coin` suggests common currency codes.

Error messages of slash commands are only shown to whoever ran the command,
and server-only commands (music, birthdays, member count) don't show up in
DMs. `/setmembercount` and `/stopmembercount` only show up for administrators.
Prefix aliases (`%hi`, `%tg`, `%tgparar`) only work with `%`. `/birthday`
works through subcommands (`/birthday add`, `/birthday list`, …), and
`/birthday help` stands in for a bare `%birthday`.

The bot registers its slash commands with Discord when it starts, but only
when they changed since the last start (Discord rate-limits this); what was
last registered is kept in `app_commands.json` in the data directory. The bot's
owner can force a new registration with `%sync`. To show up in a server, the
bot must have been invited with the `applications.commands` scope (together
with `bot`); bots invited before may need to be invited again with the new
scope. A new command can take a moment to show up, and Discord's client may
need a restart (Ctrl+R) to show changes right away.

## Music Commands 🎵

- `%play [url/search/spotify]`: Play music from YouTube URL, Spotify URL, or search term
- `%pause`: Pause/Resume the current song
- `%stop`: Stop playing and clear the queue
- `%skip`: Skip to the next song
- `%queue`: Show the current queue
- `%leave`: Leave the voice channel

Only YouTube video links, Spotify track, album and playlist links and search
terms are accepted. Spotify audio is not streamed: the bot reads each track's
name, artists and duration from Spotify's public embed page (no account needed)
and looks for the same recording on YouTube Music (official songs first, then
videos) and then YouTube. A result is only played when its title, one of its
artists and its duration (within a few seconds) match the track; otherwise the
track is skipped with a message, rather than playing a guess. Albums and
playlists queue up to 100 tracks (Spotify's embed page lists no more than
that, and the bot says so when a playlist reaches it), and each one is looked
up only when its turn comes.
Playback controls (and `%play` while music is playing) only work from the bot's
voice channel, unless the member has the *Move Members* permission.
The bot leaves the voice channel on its own after 5 minutes with nothing
playing or queued, or 2 minutes after everyone else has left it.

## TimeGuessr 🌍

- `%tg [segundos] [rodadas]`: a game with random photos from
  [TimeGuessr](https://timeguessr.com) (its free play mode and past daily
  challenges), 5 by default and up to 15: `%tg 45 10` plays 10 rounds of 45
  seconds. Photos already shown in a server aren't shown there again.
- `%tg diario [segundos] [rodadas]`: today's daily challenge, the same for
  everyone. It has 5 photos, so `rodadas` can only shorten it.
- `/timeguessr` takes the same options (`modo`, `tempo`, `rodadas`), each
  optional.
- `%tgstop`: ends the game (whoever started it, or a moderator).

Each photo has a **✏️ Palpitar** button that opens a form for the country and
the year, so nobody sees (or copies) a guess before the answer; a guess typed
in the chat is deleted (when the bot may) with a hint to use the button. A
guess can be changed until the round ends, and from the second round on, a
round ends as soon as everyone who played the earlier rounds has guessed.

Countries can be typed in Portuguese or English, as codes (`EUA`, `UK`) and
without accents, and small typos are forgiven; the country names and
coordinates come with the bot (`magna/countries.json`, from
[mledoze/countries](https://github.com/mledoze/countries), ODbL). Scoring:
up to 2,500 points for the year (100 fewer per year off) and 2,500 for the
place: all of them for the right country, otherwise fewer the farther the
guessed country's center is from the photo. The answer shows how many years
and kilometers each player was off, with links to Google Maps and, when
TimeGuessr has one, Street View.

The photos already shown are kept in `timeguessr_seen.json` in the data
directory. Only the daily challenge comes from a public endpoint; random
games read the data the site's own pages load, so a change to the site can
break them (`%tg diario` keeps working then).

## Setup 🚀

1. Install dependencies:
```bash
pip install -r requirements.txt yt-dlp
```
`requirements.txt` pins every dependency except yt-dlp, which is installed
separately so it is always the latest release (see [Dependências](#dependências)).

2. Set up environment variables in `.env`:
- `TOKEN` - Discord Bot Token
- `GEMINI_API_KEY` - Google Gemini API Key
- `GEMINI_MODEL` - (optional) Gemini model to use; defaults to `gemini-flash-latest`
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

5. Run the tests:
```bash
python -m unittest
```

Live checks against the real Spotify and YouTube services are skipped by
default (and in CI). To run them, optionally with a `spotify.link` URL shared
from the Spotify app and one of your playlists (the number of tracks read is
printed):
```bash
MAGNA_LIVE_TESTS=1 SPOTIFY_SHORT_LINK=https://spotify.link/... \
  SPOTIFY_PLAYLIST_URL=https://open.spotify.com/playlist/... \
  python -m unittest tests.test_spotify_live -v
```

## Dependências

`requirements.in` lista as dependências diretas e `requirements.txt` é gerado a
partir dele com todas as versões fixadas, então um rebuild instala sempre o
mesmo conjunto de pacotes. A exceção é o yt-dlp: o YouTube quebra versões
antigas com frequência, por isso ele fica de fora do arquivo gerado e é
instalado separadamente na versão mais recente, tanto no CI quanto na imagem.
O rebuild semanal agendado garante que a imagem publicada não fique com um
yt-dlp antigo mesmo sem novos commits.

Para adicionar uma dependência, edite `requirements.in` e gere o arquivo de
novo com [uv](https://docs.astral.sh/uv/):

```bash
uv pip compile requirements.in -o requirements.txt \
  --universal --python-version 3.11 --no-emit-package yt-dlp
```

Para atualizar as versões fixadas, rode o mesmo comando com `--upgrade` (tudo)
ou `--upgrade-package <nome>` (um pacote só) e rode os testes antes de fazer o
commit.

## Imagem no Docker Hub

Cada push na branch `main` roda os testes e, se eles passarem, publica
automaticamente uma imagem ARM64 em `docker.io/gabborges/magna-bot:latest` por
meio de um runner ARM64 nativo do GitHub Actions. Pull requests rodam apenas os
testes. O servidor não precisa receber o código-fonte nem fazer o build.
Toda segunda-feira o workflow também refaz a imagem para pegar a versão mais
recente do yt-dlp (veja [Dependências](#dependências)). Esse rebuild recebe
também uma tag com a data, como `weekly-20261019`.

Se uma versão nova do yt-dlp quebrar alguma coisa, recrie o container com a
tag da semana anterior no lugar de `latest` até sair uma correção. Lembre de
desativar a atualização automática enquanto isso, ou ela volta para a
`latest`. As tags disponíveis aparecem na página do repositório no Docker Hub.

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

# Atualização manual depois que uma nova imagem for publicada:
podman pull docker.io/gabborges/magna-bot:latest
podman rm -f magna-bot
# Execute novamente o comando "podman run" acima.
```

`podman start`/`restart` reaproveitam o container existente, com a imagem e o
`.env` da época em que ele foi criado. Para usar uma imagem nova ou um `.env`
alterado, o container precisa ser recriado.

### Atualização automática

O `podman auto-update` depende de systemd, então em sistemas com OpenRC a
atualização é feita por um script no cron. Ele baixa a `latest` e só recria o
container quando a imagem mudou. Enquanto o bot está num canal de voz ele
mantém o arquivo `data/voice_active` atualizado, e o script adia a atualização
para não cortar a música. Neste exemplo o container é gerenciado por um
`compose.yaml` com `podman-compose`:

```sh
#!/bin/sh
# /usr/local/bin/magna-bot-update
set -eu
DIR=/caminho/para/magna-bot   # diretório com compose.yaml e .env
IMAGE=docker.io/gabborges/magna-bot:latest
# O bot atualiza este arquivo a cada minuto enquanto está num canal de voz.
FLAG=$DIR/data/voice_active

podman pull -q "$IMAGE" >/dev/null
new=$(podman image inspect --format "{{.Id}}" "$IMAGE")
cur=$(podman inspect --format "{{.Image}}" magna-bot 2>/dev/null || echo none)
[ "$new" = "$cur" ] && exit 0

# Não interrompe música: tenta de novo na próxima execução. Um arquivo
# com mais de 3 minutos (bot caiu) é ignorado.
if [ -f "$FLAG" ] && [ $(( $(date +%s) - $(stat -c %Y "$FLAG") )) -lt 180 ]; then
    exit 0
fi

cd "$DIR"
podman-compose up -d --force-recreate
podman image prune -f >/dev/null
echo "$(date "+%F %T") updated to $new"
```

Crontab do root (verifica a cada 10 minutos; a saída vai para um arquivo de log):

```
*/10 * * * * flock -n /run/magna-bot-update.lock /usr/local/bin/magna-bot-update >> /var/log/magna-bot-update.log 2>&1
```

Para o container voltar depois de um reboot no OpenRC:

```bash
rc-update add podman-restart default
```

Se o repositório do Docker Hub estiver privado, autentique o Podman antes do
`pull` usando um access token do Docker Hub:

```bash
printf '%s' "$DOCKERHUB_TOKEN" | podman login docker.io -u gabborges --password-stdin
```

O `.env` e os cookies não são copiados para a imagem. `birthdays.json`,
`birthday_channels.json`, `birthday_state.json`, `membercount.json`,
`timeguessr_seen.json` e `app_commands.json` ficam no diretório `data`, portanto continuam existindo
quando o container for recriado.
