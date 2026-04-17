# Fedishare

Fedishare is a self-hostable, federated photo-sharing web app inspired by the open web ethos.
It prioritizes a **clean photo-first feed** and **chronological discovery** over opaque engagement ranking.

## Features

- User registration/login
- Profile pages (`/u/{username}`)
- Photo uploads with captions
- Follow/unfollow creators
- Chronological home feed from followed accounts
- Explore feed of recent local posts
- Minimal ActivityPub + WebFinger endpoints for fediverse discovery
- Docker + docker-compose setup for easy self-hosting

## Quick start (Docker)

```bash
docker compose up --build
```

Then open <http://localhost:8000>.

Uploaded media and sqlite DB persist in `./data` via volume mount.

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Federation endpoints (MVP)

- `/.well-known/webfinger?resource=acct:<username>@<domain>`
- `/u/<username>/actor`
- `/u/<username>/outbox`
- `/u/<username>/inbox`

> Note: This MVP exposes ActivityPub resources but does not yet implement full signature validation and delivery workers required for robust production federation.
