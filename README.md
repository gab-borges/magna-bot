# Magna Bot 🤖

Um bot de Discord feito em Python com discord.py, que junta brincadeiras e
utilidades. Ele conversa por IA com a API Gemini do Google, busca memes no
Reddit, acompanha aniversários e mostra o número de membros do servidor. Toda a
interface do bot é em português.

## Funcionalidades 🌟

- **Conversa com IA**: converse com o Magna Shanoa pelo Gemini, do Google (`%ask`)
- **Memes do Reddit**: memes aleatórios de subreddits populares (`%meme`)
- **Aniversários**: cadastra e comemora os aniversários dos membros (`%birthday`)
- **Contador de membros**: um canal que mostra quantos membros o servidor tem
- **Comandos simples**: interações básicas (`%hello`, `%ping`)
- **Dados**: rola dados de RPG (`%roll 2d20+5`)
- **Moedas**: converte valores entre moedas (`%coin 5 BRL USD`)
- **TimeGuessr**: adivinhe onde e quando as fotos foram tiradas (`%tg`, veja abaixo)
- **Música**: um player de música completo:
  - Toca links do YouTube (`%play [link]`)
  - Busca e toca músicas (`%play [busca]`)
  - Toca músicas, álbuns e playlists do Spotify (`%play [link do spotify]`)
  - Fila de músicas (`%queue`)
  - Controles (`%pause`, `%stop`, `%skip`, `%loop`)
  - Saída do canal de voz (`%leave`)

`%help` lista os comandos por categoria, e `%help <comando>` explica um comando
e seus argumentos.

## Comandos de barra ⚡

Todo comando funciona tanto com o prefixo `%` quanto como comando de barra do
Discord: `%play never gonna give you up` e `/play` fazem a mesma coisa. Os
comandos de barra mostram uma descrição de cada argumento enquanto você digita,
e alguns sugerem valores:

- `/play` sugere músicas do YouTube Music enquanto você digita; escolher uma
  toca exatamente aquela música. Um link ou qualquer outro texto funciona como
  no `%play`.
- `/coin` sugere as moedas mais comuns (pelo código ou pelo nome, com ou sem
  acento).

As mensagens de erro dos comandos de barra só aparecem para quem usou o
comando, e os comandos que só funcionam em servidores (música, aniversários,
contador de membros) não aparecem na DM. `/setmembercount` e `/stopmembercount`
só aparecem para administradores. Os apelidos de prefixo (`%hi`, `%tg`,
`%tgparar`) só funcionam com `%`. O `/birthday` funciona por subcomandos
(`/birthday add`, `/birthday list`, …), e `/birthday help` faz o papel do
`%birthday` sozinho.

O bot registra os comandos de barra no Discord quando inicia, mas só quando
eles mudaram desde a última vez (o Discord limita quantas vezes isso pode ser
feito); o que foi registrado por último fica em `app_commands.json`, no
diretório de dados. O dono do bot pode forçar um novo registro com `%sync`.
Para os comandos aparecerem num servidor, o bot precisa ter sido convidado com
o escopo `applications.commands` (junto com `bot`); bots convidados antes
disso podem precisar ser convidados de novo com o novo escopo. Um comando novo
pode demorar um pouco para aparecer, e o Discord pode precisar ser recarregado
(Ctrl+R) para mostrar as mudanças na hora.

## Comandos de música 🎵

- `%play [link/busca/spotify]`: toca uma música por link do YouTube, link do
  Spotify ou busca
- `%pause`: pausa/retoma a música atual
- `%stop`: para a música e limpa a fila
- `%skip`: pula para a próxima música
- `%loop`: liga/desliga a repetição da música atual
- `%queue`: mostra a fila
- `%leave`: sai do canal de voz

Numa busca, o bot mostra até 10 resultados: responda com o número da música ou
com `cancelar`.

Só são aceitos links de vídeos do YouTube, links de músicas, álbuns e playlists
do Spotify e buscas. O áudio do Spotify não é transmitido: o bot lê o nome, os
artistas e a duração de cada música na página pública de embed do Spotify (sem
precisar de conta) e procura a mesma gravação no YouTube Music (primeiro as
músicas oficiais, depois os vídeos) e depois no YouTube. Um resultado só é
tocado quando o título, um dos artistas e a duração (com poucos segundos de
diferença) batem com a música; se nada bater, a música é pulada com um aviso,
em vez de tocar um chute. Álbuns e playlists colocam até 100 músicas na fila (a
página de embed do Spotify não lista mais que isso, e o bot avisa quando uma
playlist chega nesse limite), e cada uma só é procurada quando chega a vez
dela.
Os controles (e o `%play` enquanto algo está tocando) só funcionam de dentro do
canal de voz do bot, a não ser que o membro tenha a permissão *Mover membros*.
O bot sai do canal de voz sozinho depois de 5 minutos sem nada tocando nem na
fila, ou 2 minutos depois que todo mundo saiu do canal.

## TimeGuessr 🌍

- `%tg [segundos] [rodadas]`: um jogo com fotos aleatórias do
  [TimeGuessr](https://timeguessr.com) (do modo livre e de desafios diários
  passados), 5 por padrão e até 15: `%tg 45 10` joga 10 rodadas de 45
  segundos. Fotos que já apareceram num servidor não aparecem de novo nele.
- `%tg diario [segundos] [rodadas]`: o desafio diário de hoje, o mesmo para
  todo mundo. Ele tem 5 fotos, então `rodadas` só pode encurtá-lo.
- `/timeguessr` aceita as mesmas opções (`modo`, `tempo`, `rodadas`), todas
  opcionais.
- `%tgstop`: encerra o jogo (quem começou ou um moderador).

Cada foto tem um botão **✏️ Palpitar** que abre um formulário para o país e o
ano, assim ninguém vê (nem copia) um palpite antes da resposta; um palpite
digitado no chat é apagado (quando o bot pode) com uma dica para usar o botão.
Dá para mudar o palpite até o fim da rodada, e a partir da segunda rodada ela
acaba assim que todo mundo que jogou as rodadas anteriores já palpitou.

Os países podem ser digitados em português ou inglês, como siglas (`EUA`, `UK`)
e sem acento, e pequenos erros de digitação são perdoados; os nomes e as
coordenadas dos países vêm com o bot (`magna/countries.json`, de
[mledoze/countries](https://github.com/mledoze/countries), ODbL). Pontuação:
até 2.500 pontos pelo ano (100 a menos por ano de diferença) e 2.500 pelo
lugar: todos pelo país certo, e menos quanto mais longe o centro do país do
palpite estiver da foto. A resposta mostra quantos anos e quilômetros cada
jogador errou, com links para o Google Maps e, quando o TimeGuessr tem, para o
Street View.

As fotos que já apareceram ficam em `timeguessr_seen.json`, no diretório de
dados. Só o desafio diário vem de um endpoint público; os jogos aleatórios
leem os dados que as próprias páginas do site carregam, então uma mudança no
site pode quebrá-los (o `%tg diario` continua funcionando nesse caso).

## Instalação 🚀

1. Instale as dependências:
```bash
pip install -r requirements.txt
```
Todas as dependências têm versão fixa, menos o yt-dlp, que sempre instala a
versão mais recente (veja [Dependências](#dependências)).

2. Configure as variáveis de ambiente no `.env`:
- `TOKEN` - token do bot no Discord
- `GEMINI_API_KEY` - chave da API Gemini, do Google
- `GEMINI_MODEL` - (opcional) modelo do Gemini; o padrão é `gemini-flash-latest`
- `REDDIT_CLIENT_ID` - Client ID da API do Reddit
- `REDDIT_CLIENT_SECRET` - Client Secret da API do Reddit
- `REDDIT_USER_AGENT` - User Agent da API do Reddit

3. Instale as dependências do sistema:
```bash
# Debian/Ubuntu
sudo apt-get update && sudo apt-get install -y ffmpeg

# Arch Linux
sudo pacman -S ffmpeg
```

4. Rode o bot:
```bash
python main.py
```
Os aniversários, o contador de membros e os outros dados salvos ficam em
`./data` (defina `DATA_DIR` para usar outro diretório).

5. Rode os testes:
```bash
python -m unittest
```

Os testes ao vivo contra o Spotify e o YouTube de verdade são pulados por
padrão (e no CI). Para rodá-los, opcionalmente com um link `spotify.link`
compartilhado pelo app do Spotify e uma das suas playlists (o número de
músicas lidas é mostrado):
```bash
MAGNA_LIVE_TESTS=1 SPOTIFY_SHORT_LINK=https://spotify.link/... \
  SPOTIFY_PLAYLIST_URL=https://open.spotify.com/playlist/... \
  python -m unittest tests.test_spotify_live -v
```

## Dependências

As dependências ficam em três arquivos:

- `requirements.in`: as dependências diretas, editado à mão.
- `requirements.lock`: gerado a partir do `requirements.in`, com todas as
  versões fixadas, então um rebuild instala sempre o mesmo conjunto de pacotes.
  O yt-dlp fica de fora.
- `requirements.txt`: editado à mão, inclui o `requirements.lock` e adiciona o
  yt-dlp sem versão. É o arquivo usado para instalar.

O yt-dlp é a exceção porque o YouTube quebra versões antigas com frequência:
toda instalação, no CI e na imagem, pega a versão mais recente dele. O rebuild
semanal agendado garante que a imagem publicada não fique com um yt-dlp antigo
mesmo sem novos commits. Numa instalação local que já tem o yt-dlp, atualize
com `pip install --upgrade -r requirements.txt`.

Para adicionar uma dependência, edite `requirements.in` e gere o lock de novo
com [uv](https://docs.astral.sh/uv/):

```bash
uv pip compile requirements.in -o requirements.lock \
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
