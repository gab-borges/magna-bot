"""YouTube URL helpers for the music player."""
import re
import urllib.parse

HOSTS = {
    'youtube.com',
    'www.youtube.com',
    'm.youtube.com',
    'music.youtube.com',
    'youtube-nocookie.com',
    'www.youtube-nocookie.com',
}
VIDEO_ID_PATTERN = re.compile(r'[A-Za-z0-9_-]{11}')


def video_id(value):
    """Return the video ID of a YouTube video URL, or None for anything else."""
    parsed = urllib.parse.urlparse(value.strip())
    if parsed.scheme not in ('http', 'https'):
        return None

    hostname = (parsed.hostname or '').lower()
    parts = [part for part in parsed.path.split('/') if part]
    if hostname == 'youtu.be':
        found = parts[0] if parts else None
    elif hostname in HOSTS:
        if parts == ['watch']:
            found = urllib.parse.parse_qs(parsed.query).get('v', [None])[0]
        elif len(parts) >= 2 and parts[0] in ('embed', 'shorts', 'live', 'v'):
            found = parts[1]
        else:
            found = None
    else:
        return None

    if not found or not VIDEO_ID_PATTERN.fullmatch(found):
        return None
    return found


def normalize_url(value):
    """Return the canonical watch URL for a YouTube video link, or None."""
    found = video_id(value)
    return f"https://www.youtube.com/watch?v={found}" if found else None


def stream_url_expiry(stream_url):
    """Return the Unix time a stream URL stops working, when it says so.

    YouTube's media URLs carry an `expire` parameter; requesting them after
    that time fails with 403 Forbidden.
    """
    query = urllib.parse.parse_qs(urllib.parse.urlparse(stream_url).query)
    try:
        return int(query['expire'][0])
    except (KeyError, IndexError, ValueError):
        return None
