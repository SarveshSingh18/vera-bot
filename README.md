# Vera Bot - Magicpin AI Challenge

A FastAPI-based bot for the Magicpin Vera AI Challenge that provides health check and metadata endpoints.

## Features

- ✅ FastAPI application
- ✅ Health check endpoint (`/v1/healthz`)
- ✅ Metadata endpoint (`/v1/metadata`)
- ✅ Context management (`/v1/context`)
- ✅ Tick endpoint (`/v1/tick`)
- ✅ Reply endpoint (`/v1/reply`)

## Tech Stack

- **Framework:** FastAPI
- **Server:** Uvicorn
- **Deployment:** Render

## Deployment

Live bot: `https://vera-bot-2rhe.onrender.com`

### Endpoints

- `GET /v1/healthz` - Health check
- `GET /v1/metadata` - Bot metadata
- `POST /v1/context` - Push context
- `POST /v1/tick` - Process tick
- `POST /v1/reply` - Handle reply

## Quick Start

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

Visit: `http://localhost:8000/v1/healthz`

## Files

- `main.py` - FastAPI application
- `requirements.txt` - Dependencies
- `Procfile` - Deployment config (Railway)

## Author

SarveshSingh18

## Challenge

Magicpin Vera AI Challenge 2026
