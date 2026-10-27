# osu-replay-viewer-server
`AKA osubot-telegram experimental renderer`

This project provides an API for rendering osu! replays using
[osu-replay-viewer-continued](https://github.com/Airkek/osu-replay-viewer-continued).
The Docker image downloads the [latest stable viewer release](https://github.com/Airkek/osu-replay-viewer-continued/releases/latest)
by default (`OSU_VIEWER_VERSION=latest`, `OSU_VIEWER_BUILD=Release`). The viewer archive is self-contained;
the Docker image includes its ICU dependency, so no separate .NET installation is required.
It was originally intended to be used together with
[osubot-telegram](https://github.com/Airkek/osubot-telegram).

## Quick Start

1. Copy the example environment file and configure it:
   ```bash
   cp .env.example .env
   # Edit .env to set paths and optional osu! API credentials
   ```

2. Run with Docker:
   ```bash
   docker-compose up -d --build
   ```

   Run the same command after pulling runner updates. To fetch a newer viewer release when using
   `latest`, rebuild with `docker compose build --no-cache`, then run `docker compose up -d`.
   GitHub's `latest` endpoint excludes pre-releases; to select one explicitly, use
   `docker compose build --no-cache --build-arg OSU_VIEWER_VERSION=v0.11.0-pre`.

The default viewer build is `Release` on Linux x64. Its Realm database is now
`client_52.realm`; the viewer migrates an existing `client_51.realm` on first startup.
Automatic database discovery supports both versions. If you set `OSU_LAZER_REALM_PATH`
explicitly, update it to the new filename after migration.

## Configuration

Environment variables can be set in `.env` file:

| Variable | Description | Default |
|----------|-------------|---------|
| `OSU_API_CLIENT_ID` | osu! API v2 OAuth application Client ID | unset |
| `OSU_API_CLIENT_SECRET` | osu! API v2 OAuth application Client Secret | unset |

Register an [osu! OAuth application](https://osu.ppy.sh/home/account/edit#new-oauth-application)
and put its Client ID and Client Secret in `.env`. Docker Compose passes them to the runner,
and the viewer subprocess inherits them as environment variables. They are not written into
per-render JSON configs or accepted from render requests. The viewer uses the API v2
`client_credentials` grant with `public` scope; a saved osu!lazer login is not required.
The runner uses the same credentials to look up beatmaps by MD5 through API v2, caching
its access token until shortly before expiry. The runner and viewer obtain their own access
tokens automatically; only one OAuth application's credentials need to be configured.
If credentials are missing or the official API lookup fails, the runner falls back to
the existing metadata mirrors. Beatmapset downloads still use mirrors.
`OSU_API_V1_KEY` is no longer used and can be removed from existing `.env` files.

Metadata mirrors are tried in order: Nerinyan, osu.direct, Mino, then
[Hinamizawa](https://mirror.hinamizawa.ai/docs). The default beatmapset download order is osu.direct,
Nerinyan, osu!gatari, Mino, [Nekoha](https://mirror.nekoha.moe/),
[BeatConnect](https://beatconnect.io/), [Sayobot](https://osu.sayobot.cn/), [osu!dl](https://osudl.org/),
[Mirria](https://mirror.lisek.cc/docs), then Hinamizawa.

Download layers are adapted from the MD5 lookup response:

| Metadata | First download sources |
|----------|------------------------|
| Ranked, approved, loved | osu.direct, then Nekoha |
| Graveyard, pending, WIP | Nekoha, then osu.direct |
| Has video | Mirria first, then the status-specific order |
| Missing or unrecognised status | Probe the ordinary mirrors together |

Each priority source above is a separate layer. osu.direct is attempted directly
for ranked, approved, and loved sets; other priority sources are probed first.
The remaining ordinary mirrors are probed concurrently, and the first to return
ZIP bytes is selected for a full download. Pending probes are cancelled and drained
before downloading. A probe requests `Range: bytes=0-3`, accepts HTTP 200 or 206,
and reads only four bytes, with a 6-second total timeout (3 seconds for connection
and socket reads). Servers that ignore Range are supported; HTML error pages are
rejected even with HTTP 200. Full downloads also reject non-ZIP responses.

Hinamizawa aggregates other mirrors and is always attempted last, without probing.
Status and video flags are optional; no extra metadata
requests are made to fill missing fields. Each replay keeps its own provider order,
including retries when the renderer reports `Beatmap not found`. A replay also keeps
its own set of attempted providers: failed probes and every started full download
are excluded from subsequent attempts. Cancelled probes and successful probes whose
provider has not downloaded yet remain candidates. Retries search all remaining
providers, including those earlier in the list than the previous winner; retrying
a replay as a new render starts with a fresh set. Exclusions apply to the configured
providers; upstream choices inside an aggregator are controlled by that service.
Maps already in the local Realm still skip lookup and download.

Hinamizawa uses `/api/v2/md5/{md5}` for
metadata and `/api/v1/hinai/d/{id}` for `.osz` bytes, since its plain `/d/{id}` returns
JSON by default. No additional credentials are needed. Requests identify the runner
with a descriptive `User-Agent`; set `USER_AGENT` to override it.
Nekoha uses `/api/download/{id}` for `.osz` bytes. Its documented API has no MD5
lookup, so it is registered only as a download provider.
Sayobot uses `/beatmaps/download/full/{id}` on `txy1.sayobot.cn`; osu!dl uses
`/s/{id}`. Both are download providers without additional credentials, and their
redirects are followed automatically. osu!dl mirrors ranked, approved, and loved
sets; some copyright-restricted archives omit audio and image assets.
Mirria uses `/d/{id}` and includes video by default.
BeatConnect uses the direct `/b/{id}` archive download without API credentials;
it is registered only as a download provider.

### Render settings added in v0.11.0-pre

`POST /renders` accepts the optional multipart field `showScoreboard` (default `false`),
matching the name and default in the [o!rdr API](https://ordr.issou.best/docs/).
It maps to the viewer's `game_settings.show_leaderboard`.

```bash
curl -X POST http://localhost:9732/renders \
  -F replayFile=@replay.osr \
  -F showScoreboard=true
```

The scoreboard needs API v2 credentials. If credentials are missing or the API is unavailable,
the viewer continues rendering without it. Existing clients can omit this field.

The runner implements a subset of o!rdr render settings. Matching options use o!rdr's field
names where supported by the viewer; this does not imply full o!rdr API compatibility.

### Local encoder settings

Encoder settings are managed locally in `src/orv_config_manager.py`, independently of render
requests:

| Constant | Viewer config under `ffmpeg_options` | Default |
|----------|--------------------------------------|---------|
| `FFMPEG_RATE_CONTROL` | `video_rate_control` | `"ConstantQuantizer"` |
| `FFMPEG_QUALITY` | `video_quality` | `26` |
| `FFMPEG_BITRATE` | `video_encoder_bitrate` | `"100M"` |
| `FFMPEG_MAX_BITRATE` | `video_encoder_max_bitrate` | `None` |
| `FFMPEG_BUFFER_SIZE` | `video_encoder_buffer_size` | `None` |
| `FFMPEG_GOP_SIZE` | `video_encoder_gop_size` | `450` |
| `FFMPEG_AUDIO_BITRATE` | `audio_encoder_bitrate` | `160000` |

`FFMPEG_RATE_CONTROL` accepts `"ConstantQuantizer"`, `"ConstantQuality"`, `"VariableBitrate"`,
and `"ConstantBitrate"`. The runner uses `h264_nvenc` with preset `p7` and CQP 26,
matching o!rdr's regular NVENC settings. Lower quality values mean higher quality (0–51).
Do not combine quality with VBR/CBR, or bitrate limits with CQP; the viewer rejects incompatible
settings. For 20 Mbit/s VBR, set `FFMPEG_RATE_CONTROL = "VariableBitrate"`, `FFMPEG_QUALITY = None`,
`FFMPEG_BITRATE = "20M"`, `FFMPEG_MAX_BITRATE = "30M"`, and `FFMPEG_BUFFER_SIZE = "40M"`.

GOP size is a positive frame count, or `None` to keep the encoder default. Audio bitrate is
an integer in bits per second. These two fields require a viewer release that includes their
support; older releases ignore them. To match o!rdr's regular NVENC settings, use
`FFMPEG_RATE_CONTROL = "ConstantQuantizer"`, `FFMPEG_QUALITY = 26`,
`FFMPEG_GOP_SIZE = 450`, and `FFMPEG_AUDIO_BITRATE = 160000`, keeping preset `p7`.

### Tests

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

## Requirements

- NVIDIA GPU with CUDA support (for NVENC encoding)
- Docker and Docker Compose
- NVIDIA Container Toolkit

Note: this repository is provided for reference and experimentation only.

**Warning — Not Production Ready**

Do not use this project "as is" in production or expose it to untrusted users.
Below are the main reasons and risks to consider:

- **Hardcoded configuration and behavior**: Many values (paths, ports,
	timeouts, or feature flags) are hardcoded. That makes the service brittle
	and difficult to adapt securely to different environments.
- **No authentication or access control**: The code does not implement
	robust authentication, authorization, or rate limiting. Exposing it can
	allow unauthorized use or abuse.
- **Insufficient input validation and sanitization**: Replay files, user
	input, or query parameters are not fully validated. Malformed or malicious
	inputs may crash the service or enable security issues.
- **Missing production-grade error handling and logging**: Errors may leak
	sensitive information or fail silently; there is no structured logging,
	monitoring, or alerting configured.
- **Dependencies and maintenance**: Third-party libraries used by the
	project may be outdated or have known vulnerabilities. The project may
	not be actively maintained or patched for security issues.

Recommended actions if you want to build on this project:

- Use it only for local testing or as a reference implementation.
- Conduct a security and code audit before deploying.
- Add authentication, authorization, and rate limiting.
- Replace hardcoded settings with a configurable system (env vars,
	configuration files) and document defaults.
- Add input validation, structured logging, and monitoring.
- Run the service in an isolated environment (container, VM) with
	resource limits and a reverse proxy in front.
- Keep dependencies up to date and run vulnerability scans.

For now, treat this repository as a learning or prototyping resource,
not as a ready-to-use production service.

## License

This project is licensed under the GNU General Public License v3.0 - see the [LICENSE](LICENSE) file for details.
