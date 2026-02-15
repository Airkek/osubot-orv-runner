# osu-replay-viewer-server
`AKA osubot-telegram experimental renderer`

This project provides an API for rendering osu! replays using
[osu-replay-viewer-continued](https://github.com/Airkek/osu-replay-viewer-continued).
It was originally intended to be used together with
[osubot-telegram](https://github.com/Airkek/osubot-telegram).

## Quick Start

1. Copy the example environment file and configure it:
   ```bash
   cp .env.example .env
   # Edit .env and set a secure POSTGRES_PASSWORD
   ```

2. Run with Docker:
   ```bash
   docker-compose up -d
   ```

## Configuration

Environment variables can be set in `.env` file:

| Variable | Description | Default |
|----------|-------------|---------|
| `POSTGRES_USER` | PostgreSQL username | `orv` |
| `POSTGRES_PASSWORD` | PostgreSQL password (required) | - |
| `POSTGRES_DB` | PostgreSQL database name | `orv_db` |

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