<p align="center"><img src="extension/icons/app.svg" width="112" alt="Airplay Flixer icon"></p>

# Airplay Flixer

A lightweight macOS menu bar companion and Chrome extension for casting web videos to Apple TV, with subtitles, playback controls and video downloads.

**[Download the latest release](https://github.com/IndieFear/airplay-flixer/releases/latest)** · [Report an issue](https://github.com/IndieFear/airplay-flixer/issues)

## Install

The release ZIP contains the Mac app, the unpacked Chrome extension and a **Start here.html** guide.

1. Download and extract the ZIP.
2. Move **Airplay Flixer.app** to **Applications**, then open it. Allow local network access when prompted.
3. Keep the **Chrome Extension** folder in a permanent location. Open `chrome://extensions`, enable **Developer mode**, click **Load unpacked** and select that folder.
4. Pin the extension. Open **Settings**, search for devices, save your default Apple TV and pair it using the code displayed on the TV.
5. Reload the video page, start playback and open the extension to cast.

The prebuilt app requires **Apple Silicon and macOS 26 or later**. It is locally signed and has not been notarized by Apple. macOS may require confirmation on first launch. [Apple installation guidance](https://support.apple.com/en-us/102445).

Install FFmpeg for HLS downloads, external subtitle synchronization and format adaptation. With Homebrew installed, run `brew install ffmpeg`.

The Mac and TV must be on the same local network. The companion prevents automatic idle system sleep during a cast or download. Display sleep is allowed. Quitting the companion interrupts its transfers.

## Features

- Detect HTTP video sources behind browser players, including HLS, MP4, MOV, MKV and WebM.
- Inspect HLS quality, declared codecs, frame rates and separate audio tracks.
- Save a default AirPlay or DLNA destination, pair Apple TV and forget an existing pairing.
- Control playback with play/pause, stop and a timeline when supported by the receiver.
- View delivered HLS resolution, declared or estimated bitrate, and recent relay transfer rate.
- Group external subtitles by language, choose the starting track and make alternative tracks in that language available through the Apple TV remote.
- Adjust external subtitle timing manually or analyze speech locally to suggest an offset.
- Search OpenSubtitles and SubDL using your own API keys.
- Download on-demand videos to Downloads, with the selected external subtitle track saved as a matching SRT file.

**⌥ ⇧ A** casts the best detected source to the saved destination.

## Video and receiver support

HLS playlists, variants, audio, embedded subtitles and ordinary AES-128 keys are relayed without video re-encoding. Direct MP4, M4V and MOV files support HTTP range requests. A blob URL alone cannot be cast. Start the page video after installing the extension so it can detect the actual network source.

MP4 with external subtitles and supported MKV/WebM sources can be remuxed to HLS using FFmpeg. Video is copied and audio is encoded as AAC. A temporary cache is capped at 6 GB and removed when playback stops. Seeking is limited to the prepared portion. A video codec unsupported by Apple TV remains unsupported in this mode.

DASH and DRM-protected streams are unsupported. Some sites depend on browser sessions or access restrictions that cannot be reproduced by the companion.

Generic DLNA playback uses AVTransport. Supported formats and controls depend on the receiver. For TVs that reject HLS, compatibility mode produces H.264/AAC in an MPEG-TS stream. Seeking is unavailable during this mode. External subtitles are not transmitted to generic DLNA receivers.

The app uses a pinned pyatv fork with URL queue support for recent tvOS versions. Compatibility still depends on the receiver, its software and the source format.

## Subtitles

Use **Subtitles** to choose a language and a starting track. Automatic selection follows the active page track, falling back to French. Alternative tracks in the same language are advertised to Apple TV and loaded as needed.

External SRT and WebVTT tracks are supported. Adding them to HLS requires a usable media timestamp. Streams with timeline discontinuities may require an embedded subtitle track instead.

During playback, choose a track and adjust its offset between −600 and +600 seconds. Applying an offset briefly reloads playback at the current position and attempts to preserve pause state.

Auto-sync runs locally using FFsubsync, NumPy and WebRTC voice activity detection. It analyzes up to two audio samples and proposes an offset only when the evidence is sufficient. Applying the suggestion is a separate action. Inconclusive results leave manual adjustment available.

Configure your own OpenSubtitles or SubDL API key under **Settings**. OpenSubtitles optionally supports account sign-in. Passwords are used to obtain a session and are not saved. Catalog quotas and availability are controlled by each provider.

Downloaded or imported tracks are selected for the next cast. During compatible HLS playback, **Add to playback** reloads the stream at the current position. Other sources need a new cast.

## Downloads

Under **Play**, choose a source and click **Download video**. Downloads continue after closing the extension popup. Reopen it to check transferred bytes or cancel.

Direct MP4, MOV, M4V, MKV and WebM files are copied. FFmpeg assembles on-demand HLS into MP4 without re-encoding. A master stream selects its highest declared quality and default matching audio track. Live streams and DASH downloads are unsupported.

The external track selected in **Subtitles** is saved alongside the video as a UTF-8 SRT file with the same base name. **Off** downloads the video alone. The SRT retains the original track timing. If the track cannot be saved, the video download continues and the extension displays a message.

Existing files are preserved by adding a number to the filename. Incomplete downloads are removed on cancellation. A percentage is not currently displayed. Direct files show the total size when provided by the server.

## Data and local access

The extension observes media requests and inspects video elements on HTTP(S) pages. Broad site permissions allow detection across embedded players and separate media hosts.

Relevant cookies, authorization headers and source URLs are kept in browser session storage and sent to the local companion when casting or downloading. Credentials are forwarded only to their captured origin. They are not copied across redirects to unrelated origins.

Native Messaging is restricted to the extension ID. The companion exposes an authenticated control API on the loopback interface. The media relay uses temporary unguessable URLs, restricted to the Mac and selected receiver. Download remuxing uses a separate loopback relay.

Preferences, pairing credentials, provider configuration and logs are stored in `~/Library/Application Support/Airplay Flixer`. Configuration files use restrictive permissions. Media URLs, cookies and pairing PINs are not logged.

Release archives contain application files and guides. Each user creates their own configuration, device pairings and provider keys.

## Build from source

Development requires macOS, Python 3.12+, Xcode Command Line Tools and Node.js. The built app embeds Python.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python -m unittest discover -s tests
npm test
.venv/bin/python scripts/build.py
.venv/bin/python tests/smoke_bundle.py
```

The lock file pins the dependency versions used for the release, including the pyatv fork. FFmpeg and ffprobe are external runtime dependencies.

The build creates the app, the unpacked extension, individual ZIPs and a combined sharing archive in `dist/`. It generates the app ICNS and extension branding from SVG sources, includes third-party notices, and records the minimum macOS version required by the compiled launcher and embedded Python.

To install a locally built app, run `python3 scripts/install.py`. To run the engine during development, use `PYTHONPATH=companion .venv/bin/python companion/entry.py`.

The extension ID is derived from the public key in its manifest. Keep it stable so Native Messaging continues to authorize the extension.

## Updates and removal

For an unpacked extension, click **Reload** in `chrome://extensions`. Replacing the companion while casting interrupts playback.

To remove the app, quit it, remove the extension and move the app to the Trash. Its Chrome bridge is at `~/Library/Application Support/Google/Chrome/NativeMessagingHosts/com.airplay_flixer.companion.json`. Remove the app support folder if you also want to delete preferences, provider configuration and pairings.
