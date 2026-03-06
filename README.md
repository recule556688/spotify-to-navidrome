# Spotify to Navidrom Playlist Import

Import a Spotify playlist into your Navidrom server by matching tracks (artist + title) to your library and creating a new playlist with the same name.

## Requirements

- Python 3.8+
- A Spotify app (Client ID + Secret) for API access
- A Navidrom server with a user that can create playlists and search the library

## Setup

### 1. Spotify app

1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
2. Create an app and copy the **Client ID** and **Client Secret**.

**Note:** This script uses **Client Credentials** auth, which only works for **public** playlists. To import **private** playlists you would need to add OAuth (user login) and the `playlist-read-private` scope; that is not implemented in this script.

### 2. Navidrom

Use your Navidrom base URL (e.g. `https://music.example.com`), username, and password for a user that is allowed to create playlists and search the library.

### 3. Environment configuration

Copy the example env file and fill in your values:

```bash
cp .env.example .env
```

Edit `.env`:

- `SPOTIFY_CLIENT_ID` – from the Spotify app
- `SPOTIFY_CLIENT_SECRET` – from the Spotify app
- `NAVIDROM_URL` – base URL of your Navidrom server (no trailing slash)
- `NAVIDROM_USER` – your Navidrom username
- `NAVIDROM_PASSWORD` – your Navidrom password

### 4. Install dependencies

```bash
cd spotify-to-navidrom
pip install -r requirements.txt
```

## Usage

```bash
python spotify_to_navidrom.py "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
```

You can pass either a full Spotify playlist URL or just the playlist ID.

**Options:**

- `--playlist-name NAME` – Use a different name for the Navidrom playlist (default: Spotify playlist name).
- `--dry-run` – Fetch the Spotify playlist and match tracks against Navidrom only; do not create the playlist.
- `--verbose` / `-v` – Print match details for each track.
- `--update-existing` – If a playlist with the same name already exists on Navidrom, **add only the newly matched songs** to it (no duplicate playlist). New songs are appended at the end. Use this when you run the script again after adding previously missing files to your library.

**Example:**

```bash
python spotify_to_navidrom.py "https://open.spotify.com/playlist/abc123" --playlist-name "My Import" --verbose
```

## How it works

1. The script reads the Spotify playlist (name + all tracks) via the Spotify Web API.
2. For each track it gets artist and title, then searches your Navidrom library with the Subsonic `search3` API and takes the first matching song.
3. It creates a new playlist on Navidrom with the same name and adds the matched Navidrom song IDs in order.

Tracks that are **not found** are listed at the end. “Not found” means no matching song was returned by Navidrom search for that artist/title—usually because the track is not in your Navidrom library (different tags, missing file, or different release).

## Troubleshooting

- **Spotify 401 / Invalid client:** Check `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` in `.env`.
- **Spotify 404:** The playlist ID or URL may be wrong, or the playlist may be private (this script only supports public playlists with Client Credentials).
- **Navidrom 401:** Check `NAVIDROM_URL`, `NAVIDROM_USER`, and `NAVIDROM_PASSWORD`.
- **Many “not found” tracks:** Your library may not contain those tracks, or artist/title tags may differ from Spotify. Use `--verbose` to see which Navidrom song was matched for each track.
