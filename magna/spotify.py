"""Resolve Spotify track links to a "track artists" query for YouTube.

Only metadata is read, from Spotify's public pages; no credentials are used.
"""
import asyncio
import html
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


def is_fetchable_url(url):
    """Return whether the bot may request this URL while resolving a track."""
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


def normalize_track_url(value):
    """Normalize supported Spotify track references to a web URL."""
    value = value.strip()
    uri_match = re.fullmatch(r'spotify:track:([A-Za-z0-9]{22})', value, re.IGNORECASE)
    if uri_match:
        return f"{TRACK_URL_PREFIX}{uri_match.group(1)}"

    parsed = urllib.parse.urlparse(value)
    hostname = (parsed.hostname or '').lower()
    if hostname == 'spotify.link' or hostname.endswith('.spotify.link'):
        # Short links are resolved by following their redirects, so only
        # accept ones that can be fetched safely.
        return value if is_fetchable_url(value) else None

    if not (
        hostname == 'spotify.com'
        or hostname.endswith('.spotify.com')
    ):
        return None

    parts = [part for part in parsed.path.split('/') if part]
    try:
        track_index = parts.index('track')
        track_id = parts[track_index + 1]
    except (ValueError, IndexError):
        return None

    if not TRACK_ID_PATTERN.fullmatch(track_id):
        return None

    return f"{TRACK_URL_PREFIX}{track_id}"


def query_from_embed(page_html, track_id):
    """Build a "track artists" query from the track's embed page.

    The embed page carries the track's metadata as Next.js data, including
    every artist, which oEmbed and the regular page title don't reliably do.
    """
    script = BeautifulSoup(page_html, 'html.parser').find('script', id='__NEXT_DATA__')
    if script is None or not script.string:
        return None

    try:
        entity = json.loads(script.string)['props']['pageProps']['state']['data']['entity']
    except (ValueError, KeyError, TypeError):
        return None

    if not isinstance(entity, dict):
        return None
    if entity.get('type') != 'track' or entity.get('uri') != f"spotify:track:{track_id}":
        return None

    name = (entity.get('name') or '').strip()
    artists = [
        artist.get('name', '').strip()
        for artist in entity.get('artists') or []
        if isinstance(artist, dict)
    ]
    artists = [artist for artist in artists if artist]
    if not name or not artists:
        return None
    return " ".join([name, *artists])


async def resolve_track(url, *, user_agent):
    """Resolve a Spotify track to a title/artist query for YouTube."""
    track_url = normalize_track_url(url)
    if not track_url:
        return None

    timeout = aiohttp.ClientTimeout(total=8, connect=5, sock_read=5)
    headers = {
        'User-Agent': user_agent,
        'Accept-Language': 'en-US,en;q=0.9',
    }

    try:
        # Keep every metadata attempt under one deadline so a Spotify
        # outage cannot hold the command open indefinitely.
        async with asyncio.timeout(12):
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                if not track_url.startswith(TRACK_URL_PREFIX):
                    # A short link: follow it to find out which track it is.
                    page = await fetch_page(session, track_url)
                    resolved_url = page and normalize_track_url(page[0])
                    if resolved_url:
                        track_url = resolved_url

                if track_url.startswith(TRACK_URL_PREFIX):
                    track_id = track_url.removeprefix(TRACK_URL_PREFIX)
                    embed_url = f"https://open.spotify.com/embed/track/{track_id}"
                    page = await fetch_page(session, embed_url)
                    search_query = page and query_from_embed(page[1], track_id)
                    if search_query:
                        return search_query

                # oEmbed is an official, credential-free metadata endpoint.
                # It only has the track name, so it is the last resort.
                oembed_url = 'https://open.spotify.com/oembed'
                async with session.get(
                    oembed_url, params={'url': track_url}, allow_redirects=False
                ) as response:
                    if response.status != 200:
                        return None

                    metadata = await response.json(content_type=None)
                    iframe_url = metadata.get('iframe_url', '')
                    if '/track/' not in iframe_url:
                        return None

                    title = html.unescape(metadata.get('title', '')).strip()
                    return title or None
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
        logger.warning("Could not resolve Spotify track %s: %s", url, e)
        return None
