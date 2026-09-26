# LLM Photo Organizer

Local-first photo and video review app for external drives. The UI is built with TanStack Start; file-system access, media processing, vLLM calls, and approved moves belong in the Rust engine. X31 is the default drive on the development Mac, configured locally.

## Architecture

```text
web (TanStack Start + React + TypeScript)
        │ JSON API on localhost:8040
        ▼
engine (Rust + Axum)
        ├── reads selected folders from /Volumes/X31
        ├── sends selected media to a configured vLLM API for classification
        └── moves files only after a day and its destination are approved
```

The app groups media by day and proposes `YYYY/YYYY_MM/YYMMDD_category/`. A classification result only suggests a category. Reviewing or approving a folder does not move anything; moving is a separate action.

## Requirements

- Node.js 20+ and npm
- Rust 1.85+ (edition 2024)
- `ffmpeg` and `ffprobe` for video frame extraction (up to 32 frames/request)
- X31 mounted at `/Volumes/X31` (or set `PHOTO_ROOT`)
- OpenAI-compatible vLLM API (local example default `http://127.0.0.1:8000/v1`)

## Local development

Install the UI dependencies with `npm install`, then run both processes in separate terminals from the project root:

```sh
npm run dev
```

```sh
cargo run -p llm-photo-organizer-engine
```

Open <http://127.0.0.1:3000>. The Rust API listens on `127.0.0.1:8040` by default and allows browser requests from the local development UI only.

## Configuration

| Variable | Default | Used by |
| --- | --- | --- |
| `PHOTO_ROOT` | `/Volumes/X31` | Rust engine |
| `MODEL_API` | `http://127.0.0.1:8000/v1` | Rust engine |
| `MODEL_NAME` | `openbmb/MiniCPM-V-4.6` | Rust engine |
| `ENGINE_BIND` | `127.0.0.1` | Rust engine; keep local-only for a single-user app |
| `ENGINE_PORT` | `8040` | Rust engine |
| `VITE_ENGINE_ORIGIN` | `http://127.0.0.1:8040` | Browser UI |

Generic engine settings are in [engine/.env.example](engine/.env.example). Keep personal API addresses in the ignored `engine/.env` file; Rust reads environment variables directly, so load local settings before launching it:

```sh
set -a
source engine/.env
set +a
cargo run -p llm-photo-organizer-engine
```

## Privacy and repository hygiene

Personal media, local network addresses, local environment files, generated data, and Rust build output are excluded from Git. The public source repository should contain code and generic sample configuration only. Photos are never uploaded as part of repository publishing; a selected image or extracted video frame is sent to the configured inference endpoint only when the user requests analysis.

## Project status

Initial scaffold: TanStack Start is initialized, the Rust/Axum workspace and local configuration surface are in place, and the dashboard currently shows engine and drive discovery status. Recursive scanning, day-level and per-image classification, review persistence, and approved moves are the next implementation steps. No X31 scan or file move runs automatically.
