#!/usr/bin/env python3
"""
Import a Spotify playlist to Navidrom by matching tracks via Subsonic search
and creating a new playlist with the same name.
"""

import argparse
import re
import sys
import requests
from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

# Subsonic API version used by Navidrom
SUBSONIC_VERSION = "1.16.1"
CLIENT_NAME = "spotify-to-navidrom"
# Batch size when adding songs via updatePlaylist (avoids URL length limits)
SONG_BATCH_SIZE = 50


def load_config():
    """Load configuration from environment (and .env file if present)."""
    load_dotenv()
    required = {
        "SPOTIFY_CLIENT_ID": "Spotify Client ID",
        "SPOTIFY_CLIENT_SECRET": "Spotify Client Secret",
        "NAVIDROM_URL": "Navidrom base URL (e.g. https://music.example.com)",
        "NAVIDROM_USER": "Navidrom username",
        "NAVIDROM_PASSWORD": "Navidrom password",
    }
    config = {}
    for key, label in required.items():
        val = __import__("os").environ.get(key)
        if not val or not val.strip():
            print(f"Error: Missing {key}. {label}. Set in .env or environment.", file=sys.stderr)
            sys.exit(1)
        config[key] = val.strip()
    # Normalize Navidrom URL (no trailing slash)
    config["NAVIDROM_URL"] = config["NAVIDROM_URL"].rstrip("/")
    return config


def extract_spotify_playlist_id(value: str) -> str:
    """Extract playlist ID from a Spotify URL or return value if it's already an ID."""
    value = value.strip()
    # URL forms: https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M
    match = re.search(r"open\.spotify\.com/playlist/([a-zA-Z0-9]+)", value)
    if match:
        return match.group(1)
    # Assume it's a raw ID (alphanumeric)
    if re.match(r"^[a-zA-Z0-9]+$", value):
        return value
    print("Error: Could not parse Spotify playlist ID or URL.", file=sys.stderr)
    sys.exit(1)


def get_spotify_playlist(sp, playlist_id: str):
    """Fetch playlist metadata and all tracks from Spotify. Returns (name, list of (artist, title))."""
    try:
        playlist = sp.playlist(playlist_id)
    except Exception as e:
        print(f"Error fetching Spotify playlist: {e}", file=sys.stderr)
        sys.exit(1)
    name = playlist.get("name") or "Imported from Spotify"
    tracks = []
    offset = 0
    limit = 50
    while True:
        resp = sp.playlist_tracks(playlist_id, limit=limit, offset=offset)
        items = resp.get("items") or []
        for item in items:
            track = item.get("track")
            if not track or track.get("type") != "track":
                continue
            title = (track.get("name") or "").strip()
            artists = track.get("artists") or []
            artist = ", ".join((a.get("name") or "").strip() for a in artists).strip()
            if title or artist:
                tracks.append((artist, title))
        total = resp.get("total") or 0
        offset += len(items)
        if offset >= total or len(items) == 0:
            break
    return name, tracks


def navidrom_request(base_url: str, user: str, password: str, endpoint: str, params: dict):
    """Call Navidrom Subsonic REST API. Returns JSON body of subsonic-response or None on error."""
    url = f"{base_url}/rest/{endpoint}"
    auth = {"u": user, "p": password, "v": SUBSONIC_VERSION, "c": CLIENT_NAME, "f": "json"}
    all_params = {**auth, **params}
    try:
        r = requests.get(url, params=all_params, timeout=30)
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        print(f"Navidrom request failed: {e}", file=sys.stderr)
        return None
    except ValueError as e:
        print(f"Navidrom response not JSON: {e}", file=sys.stderr)
        return None
    resp = data.get("subsonic-response") or data
    if resp.get("status") != "ok":
        err = resp.get("error", {})
        msg = err.get("message", "Unknown error")
        code = err.get("code", 0)
        print(f"Navidrom API error ({code}): {msg}", file=sys.stderr)
        return None
    return resp


def navidrom_search(base_url: str, user: str, password: str, query: str, song_count: int = 5):
    """Search Navidrom via search3. Returns list of song dicts with 'id', 'title', 'artist'."""
    result = navidrom_request(
        base_url, user, password, "search3.view", {"query": query, "songCount": song_count}
    )
    if not result:
        return []
    sr = result.get("searchResult3") or {}
    songs = sr.get("song") or []
    if isinstance(songs, dict):
        songs = [songs]
    return songs


def find_navidrom_song_id(base_url: str, user: str, password: str, artist: str, title: str, verbose: bool):
    """Search Navidrom for a track by artist and title; return first matching song id or None."""
    query = f"{artist} {title}".strip() or title or artist
    if not query:
        return None
    songs = navidrom_search(base_url, user, password, query, song_count=5)
    if not songs:
        if verbose:
            print(f"  No match: {artist} – {title}", file=sys.stderr)
        return None
    first = songs[0]
    sid = first.get("id")
    if verbose:
        print(f"  Matched: {artist} – {title} -> {first.get('title')} by {first.get('artist')} (id={sid})", file=sys.stderr)
    return sid


def get_navidrom_playlists(base_url: str, user: str, password: str):
    """Return list of playlists: [{id, name, ...}, ...]."""
    result = navidrom_request(base_url, user, password, "getPlaylists.view", {})
    if not result:
        return []
    playlists = result.get("playlists") or {}
    p = playlists.get("playlist")
    if isinstance(p, list):
        return p
    return [p] if p else []


def get_navidrom_playlist_song_ids(base_url: str, user: str, password: str, playlist_id: str):
    """Return list of song IDs in the playlist (order preserved)."""
    result = navidrom_request(base_url, user, password, "getPlaylist.view", {"id": playlist_id})
    if not result:
        return []
    pl = result.get("playlist") or {}
    entries = pl.get("entry") or []
    if isinstance(entries, dict):
        entries = [entries]
    return [e.get("id") for e in entries if e.get("id")]


def create_navidrom_playlist(base_url: str, user: str, password: str, name: str, song_ids: list):
    """
    Create a Navidrom playlist with the given name and song IDs in order.
    Uses createPlaylist with songIds; if too many, creates empty then updatePlaylist in batches.
    """
    if not song_ids:
        # Create empty playlist
        result = navidrom_request(
            base_url, user, password, "createPlaylist.view", {"name": name}
        )
        if result and result.get("playlist"):
            return result["playlist"].get("id")
        return None
    if len(song_ids) <= SONG_BATCH_SIZE:
        params = {"name": name, "songId": song_ids}
        result = navidrom_request(base_url, user, password, "createPlaylist.view", params)
        if result and result.get("playlist"):
            return result["playlist"].get("id")
        return None
    # Many songs: create empty then add in batches
    result = navidrom_request(base_url, user, password, "createPlaylist.view", {"name": name})
    if not result or not result.get("playlist"):
        return None
    playlist_id = result["playlist"].get("id")
    for i in range(0, len(song_ids), SONG_BATCH_SIZE):
        batch = song_ids[i : i + SONG_BATCH_SIZE]
        params = {"playlistId": playlist_id, "songIdToAdd": batch}
        ok = navidrom_request(base_url, user, password, "updatePlaylist.view", params)
        if not ok:
            return playlist_id  # Return id anyway; some songs may have been added
    return playlist_id


def add_songs_to_navidrom_playlist(
    base_url: str, user: str, password: str, playlist_id: str, song_ids: list
):
    """Append song IDs to an existing playlist in batches. Returns True on success."""
    for i in range(0, len(song_ids), SONG_BATCH_SIZE):
        batch = song_ids[i : i + SONG_BATCH_SIZE]
        params = {"playlistId": playlist_id, "songIdToAdd": batch}
        ok = navidrom_request(base_url, user, password, "updatePlaylist.view", params)
        if not ok:
            return False
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Import a Spotify playlist to Navidrom by matching tracks and creating a new playlist."
    )
    parser.add_argument(
        "spotify_playlist",
        help='Spotify playlist URL or ID. On Windows/PowerShell, wrap the URL in double quotes if it contains "&" (e.g. "https://open.spotify.com/playlist/...").',
    )
    parser.add_argument(
        "--playlist-name",
        default=None,
        help="Override the Navidrom playlist name (default: use Spotify playlist name)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only fetch from Spotify and match; do not create playlist on Navidrom",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print match details for each track",
    )
    parser.add_argument(
        "--update-existing",
        action="store_true",
        help="If a playlist with the same name exists on Navidrom, add only newly matched songs to it (no duplicate playlist). New songs are appended at the end.",
    )
    args = parser.parse_args()

    config = load_config()
    playlist_id = extract_spotify_playlist_id(args.spotify_playlist)

    # Spotify
    auth = SpotifyClientCredentials(
        client_id=config["SPOTIFY_CLIENT_ID"],
        client_secret=config["SPOTIFY_CLIENT_SECRET"],
    )
    sp = spotipy.Spotify(auth_manager=auth)
    playlist_name, tracks = get_spotify_playlist(sp, playlist_id)
    if args.playlist_name:
        playlist_name = args.playlist_name
    print(f"Spotify playlist: {playlist_name!r} ({len(tracks)} tracks)")

    # Match each track to Navidrom
    base_url = config["NAVIDROM_URL"]
    user = config["NAVIDROM_USER"]
    password = config["NAVIDROM_PASSWORD"]
    song_ids = []
    not_found = []
    for artist, title in tracks:
        sid = find_navidrom_song_id(base_url, user, password, artist, title, args.verbose)
        if sid:
            song_ids.append(sid)
        else:
            not_found.append(f"{artist} – {title}")

    print(f"Matched: {len(song_ids)} / {len(tracks)}")
    if not_found:
        print(f"Not found in Navidrom library ({len(not_found)}):")
        for line in not_found:
            print(f"  {line}")

    if args.dry_run:
        print("Dry run: skipping playlist creation.")
        return

    if not song_ids:
        print("No tracks to add. Create an empty playlist anyway? (y/N)", end=" ")
        try:
            if input().strip().lower() != "y":
                print("Aborted.")
                sys.exit(0)
        except EOFError:
            print("Aborted (no input).")
            sys.exit(0)

    if args.update_existing:
        playlists = get_navidrom_playlists(base_url, user, password)
        existing = next((p for p in playlists if (p.get("name") or "").strip() == playlist_name), None)
        if existing:
            existing_id = existing.get("id")
            current_ids = get_navidrom_playlist_song_ids(base_url, user, password, existing_id)
            current_set = set(current_ids)
            new_song_ids = [sid for sid in song_ids if sid not in current_set]
            if not new_song_ids:
                print(f"Playlist {playlist_name!r} already exists (id={existing_id}). No new songs to add.")
                return
            if add_songs_to_navidrom_playlist(base_url, user, password, existing_id, new_song_ids):
                print(f"Updated existing playlist {playlist_name!r} (id={existing_id}): added {len(new_song_ids)} new tracks (now {len(current_ids) + len(new_song_ids)} total).")
            else:
                print("Failed to add songs to existing playlist.", file=sys.stderr)
                sys.exit(1)
            return
        # No existing playlist with this name; fall through to create new

    pid = create_navidrom_playlist(base_url, user, password, playlist_name, song_ids)
    if pid:
        print(f"Created Navidrom playlist: {playlist_name!r} (id={pid}) with {len(song_ids)} tracks.")
    else:
        print("Failed to create playlist on Navidrom.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
