"""Read Spotify tracks, albums and playlists from Spotify's public pages.

Only metadata is read, from the embed pages Spotify serves to other sites; no
credentials are used. Each track is {'name', 'artists', 'duration_ms'}, which
is what's needed to find the same recording on YouTube.
"""
import asyncio
import json
import logging
import re
import urllib.parse

import aiohttp
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HOSTS = {'spotify.com', 'spotify.link'}
MAX_REDIRECTS = 5
TRACK_URL_PREFIX = "https://open.spotify.com/track/"
TRACK_ID_PATTERN = re.compile(r'[A-Za-z0-9]{22}')
COLLECTION_KINDS = ('album', 'playlist')


def is_spotify_reference(value):
    """Return whether a value is a Spotify URL or URI."""
    value = value.strip()
    if value.lower().startswith('spotify:'):
        return True

    parsed = urllib.parse.urlparse(value)
    hostname = (parsed.hostname or '').lower()
    return (
        hostname == 'spotify.com'
        or hostname.endswith('.spotify.com')
        or hostname == 'spotify.link'
        or hostname.endswith('.spotify.link')
    )


def is_short_link(value):
    hostname = (urllib.parse.urlparse(value.strip()).hostname or '').lower()
    return hostname == 'spotify.link' or hostname.endswith('.spotify.link')


def is_fetchable_url(url):
    """Return whether the bot may request this URL while resolving a link."""
    parsed = urllib.parse.urlparse(url)
    hostname = (parsed.hostname or '').lower()
    try:
        port = parsed.port
    except ValueError:
        return False
    is_spotify_host = (
        hostname in HOSTS
        or hostname.endswith(('.spotify.com', '.spotify.link'))
    )
    return (
        parsed.scheme == 'https'
        and port in (None, 443)
        and not parsed.username
        and is_spotify_host
    )


async def fetch_page(session, url):
    """GET a Spotify page, following redirects only within Spotify over HTTPS.

    Returns (final_url, html), or None when the page can't be fetched.
    """
    for _ in range(MAX_REDIRECTS + 1):
        if not is_fetchable_url(url):
            logger.warning("Refusing to follow Spotify redirect to %s", url)
            return None

        async with session.get(url, allow_redirects=False) as response:
            location = response.headers.get('Location')
            if response.status in (301, 302, 303, 307, 308) and location:
                url = urllib.parse.urljoin(url, location)
                continue
            if response.status != 200:
                return None
            return url, await response.text()

    logger.warning("Too many redirects while resolving Spotify link")
    return None


def entity(value, kinds):
    """Return (kind, id) for a Spotify URL or URI of one of the given kinds."""
    value = value.strip()
    uri_match = re.fullmatch(r'spotify:([a-z]+):([A-Za-z0-9]{22})', value, re.IGNORECASE)
    if uri_match:
        kind = uri_match.group(1).lower()
        return (kind, uri_match.group(2)) if kind in kinds else None

    parsed = urllib.parse.urlparse(value)
    hostname = (parsed.hostname or '').lower()
    if not (hostname == 'spotify.com' or hostname.endswith('.spotify.com')):
        return None

    parts = [part for part in parsed.path.split('/') if part]
    for index, part in enumerate(parts[:-1]):
        if part in kinds:
            entity_id = parts[index + 1]
            if TRACK_ID_PATTERN.fullmatch(entity_id):
                return part, entity_id
            return None
    return None


def normalize_track_url(value):
    """Normalize supported Spotify track references to a web URL."""
    if is_short_link(value):
        # Short links are resolved by following their redirects, so only
        # accept ones that can be fetched safely.
        value = value.strip()
        return value if is_fetchable_url(value) else None

    found = entity(value, ('track',))
    return f"{TRACK_URL_PREFIX}{found[1]}" if found else None


def embed_entity(page_html, kind, entity_id):
    """Return the entity described by an embed page, if it is the one asked for.

    The embed page carries the entity's metadata as Next.js data, including
    every artist, which oEmbed and the regular page title don't reliably do.
    """
    script = BeautifulSoup(page_html, 'html.parser').find('script', id='__NEXT_DATA__')
    if script is None or not script.string:
        return None

    try:
        found = json.loads(script.string)['props']['pageProps']['state']['data']['entity']
    except (ValueError, KeyError, TypeError):
        return None

    if not isinstance(found, dict):
        return None
    if found.get('type') != kind or found.get('uri') != f"spotify:{kind}:{entity_id}":
        return None
    return found


def duration_ms(value):
    is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
    return int(value) if is_number and value > 0 else None


def track_from_embed(page_html, track_id):
    """Read a track's name, artists and duration from its embed page."""
    found = embed_entity(page_html, 'track', track_id)
    if found is None:
        return None

    name = " ".join(str(found.get('name') or '').split())
    artists = [
        " ".join(artist['name'].split())
        for artist in found.get('artists') or []
        if isinstance(artist, dict) and isinstance(artist.get('name'), str)
    ]
    artists = [artist for artist in artists if artist]
    if not name or not artists:
        return None
    return {'name': name, 'artists': artists, 'duration_ms': duration_ms(found.get('duration'))}


def collection_from_embed(page_html, kind, entity_id):
    """Read an album's or playlist's name and tracks from its embed page.

    Returns {'name', 'tracks'}, or None when the page has no usable tracks.
    """
    found = embed_entity(page_html, kind, entity_id)
    if found is None or not isinstance(found.get('trackList'), list):
        return None

    tracks = []
    for item in found['trackList']:
        if not isinstance(item, dict):
            continue
        # Skip podcast episodes and local files, which aren't on YouTube.
        if not re.fullmatch(r'spotify:track:[A-Za-z0-9]{22}', item.get('uri') or ''):
            continue

        name = " ".join(str(item.get('title') or item.get('name') or '').split())
        if isinstance(item.get('artists'), list):
            artists = [
                artist['name']
                for artist in item['artists']
                if isinstance(artist, dict) and isinstance(artist.get('name'), str)
            ]
        else:
            # The list shows artists as one "A, B" line.
            artists = str(item.get('subtitle') or '').split(',')
        artists = [" ".join(artist.split()) for artist in artists]
        artists = [artist for artist in artists if artist]
        # Without artists a YouTube result can't be checked, so skip it.
        if not name or not artists:
            continue

        tracks.append({
            'name': name,
            'artists': artists,
            'duration_ms': duration_ms(item.get('duration')),
        })

    if not tracks:
        return None
    name = " ".join(str(found.get('name') or found.get('title') or '').split())
    return {'name': name or kind.capitalize(), 'tracks': tracks}


def track_title(track):
    return f"{track['name']} — {', '.join(track['artists'])}"


def session(user_agent):
    timeout = aiohttp.ClientTimeout(total=8, connect=5, sock_read=5)
    headers = {
        'User-Agent': user_agent,
        'Accept-Language': 'en-US,en;q=0.9',
    }
    return aiohttp.ClientSession(timeout=timeout, headers=headers)


async def follow_short_link(url, *, user_agent):
    """Return the Spotify URL a spotify.link short link points to, or None."""
    url = url.strip()
    if not is_fetchable_url(url):
        return None
    try:
        async with asyncio.timeout(12):
            async with session(user_agent) as client:
                page = await fetch_page(client, url)
                return page[0] if page else None
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.warning("Could not resolve Spotify short link %s: %s", url, e)
        return None


async def resolve_collection(url, *, user_agent):
    """Resolve a Spotify album or playlist to its name and tracks."""
    found = entity(url, COLLECTION_KINDS)
    if found is None:
        return None

    kind, entity_id = found
    embed_url = f"https://open.spotify.com/embed/{kind}/{entity_id}"
    try:
        async with asyncio.timeout(12):
            async with session(user_agent) as client:
                page = await fetch_page(client, embed_url)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
        logger.warning("Could not resolve Spotify %s %s: %s", kind, url, e)
        return None

    return page and collection_from_embed(page[1], kind, entity_id)


async def resolve_track(url, *, user_agent):
    """Resolve a Spotify track to its name, artists and duration.

    There is no fallback to Spotify's oEmbed endpoint: it only has the track
    name, and a YouTube result can't be checked against a name alone.
    """
    track_url = normalize_track_url(url)
    if not track_url:
        return None

    try:
        # Keep every request under one deadline so a Spotify outage cannot
        # hold the command open indefinitely.
        async with asyncio.timeout(12):
            async with session(user_agent) as client:
                if not track_url.startswith(TRACK_URL_PREFIX):
                    # A short link: follow it to find out which track it is.
                    page = await fetch_page(client, track_url)
                    track_url = page and normalize_track_url(page[0])
                    if not track_url or not track_url.startswith(TRACK_URL_PREFIX):
                        return None

                track_id = track_url.removeprefix(TRACK_URL_PREFIX)
                embed_url = f"https://open.spotify.com/embed/track/{track_id}"
                page = await fetch_page(client, embed_url)
                return page and track_from_embed(page[1], track_id)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
        logger.warning("Could not resolve Spotify track %s: %s", url, e)
        return None
