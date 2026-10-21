# MUNIX Bot Hosting

A lightweight Python dashboard for managing trusted Python Telegram and Discord bot projects on a persistent server.

## Features
- Owner login using environment variables
- Upload ZIP bot projects
- Start, stop, restart, and delete bot processes
- View recent process logs
- SQLite project metadata
- Docker deployment starter

## Run
1. Copy `.env.example` to `.env`.
2. Set a strong `SECRET_KEY` and `ADMIN_PASSWORD`.
3. Install dependencies with `pip install -r requirements.txt`.
4. Run `python app.py` and open `http://127.0.0.1:8000`.

## Hosting note
Use a persistent host that allows long-running background processes. Serverless and WebAssembly runtimes may not support child processes or guarantee 24/7 uptime. Upload only code you trust: bot projects can execute arbitrary commands with the permissions of the hosting account.
