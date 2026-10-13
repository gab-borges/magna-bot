"""Decide whether a YouTube result is the same recording as a Spotify track.

Spotify links are played by finding the track on YouTube. Taking the first
search result plays covers, live versions or unrelated videos often enough to
annoy, so every candidate has to match the track's title, one of its artists
and its duration. When none does, the track is skipped rather than guessed.
"""
import re
import unicodedata

# Recordings can differ by a second or two between services; music videos
# with intros or outros differ by far more and are rejected.
DURATION_TOLERANCE_SECONDS = 7

# A word that marks a different recording when only one side has it.
VERSION_WORDS = {
    'acapella', 'acoustic', 'cover', 'instrumental', 'karaoke', 'live',
    'mashup', 'nightcore', 'remix', 'reverb', 'slowed', 'sped', 'unplugged',
}

BRACKETED = re.compile(r'\([^)]*\)|\[[^\]]*\]')


def normalize(text):
    """Lowercase, drop accents and punctuation, and collapse whitespace."""
    text = unicodedata.normalize('NFKD', text or '')
    text = ''.join(char for char in text if not unicodedata.combining(char))
    text = text.casefold().replace('&', ' and ')
    return ' '.join(re.findall(r'\w+', text))


def contains_phrase(text, phrase):
    return bool(phrase) and f" {phrase} " in f" {text} "


def core_title(title):
    """The title without bracketed notes or a " - Remastered" style suffix."""
    core = BRACKETED.sub(' ', title or '').split(' - ')[0]
    return normalize(core) or normalize(title)


def title_matches(track_name, candidate_title):
    candidate = normalize(candidate_title)
    if not contains_phrase(candidate, core_title(track_name)):
        return False

    track_words = set(normalize(track_name).split())
    candidate_words = set(candidate.split())
    return all(
        (word in track_words) == (word in candidate_words)
        for word in VERSION_WORDS
    )


def artist_matches(track_artists, candidate_text):
    candidate = normalize(candidate_text)
    return any(contains_phrase(candidate, normalize(artist)) for artist in track_artists)


def duration_matches(track_duration_ms, candidate_seconds):
    if not track_duration_ms:
        return True  # Spotify didn't say; title and artist must carry it.
    if not candidate_seconds:
        return False
    return abs(track_duration_ms / 1000 - candidate_seconds) <= DURATION_TOLERANCE_SECONDS


def is_match(track, candidate):
    """Whether a candidate is the track.

    track: {'name', 'artists', 'duration_ms'} read from Spotify.
    candidate: {'title', 'artist_text', 'duration'}, where artist_text is
    every text that may name the artist (credited artists, channel, title).
    """
    return (
        bool(track['artists'])
        and title_matches(track['name'], candidate['title'])
        and artist_matches(track['artists'], candidate['artist_text'])
        and duration_matches(track.get('duration_ms'), candidate.get('duration'))
    )


def pick_match(track, candidates):
    """The first candidate, in search ranking order, that is the track."""
    return next((candidate for candidate in candidates if is_match(track, candidate)), None)
