# Penny Customer Care

Penny Customer Care is a voice-first financial literacy support app for kids and families. A child earns coins through chores, reaches a learning threshold, sees personalized investment recommendations, and can call Penny for help. Parents stay in control through approval workflows, and admins can monitor calls, transcripts, traces, and approval outcomes.

This repository started from an earlier support-automation prototype and now centers on the Penny experience: role-aware login, child and parent dashboards, outbound Bland calls, Ghost-backed support data, and a live-answer path that can combine stored context with an LLM.

## What The App Does

- lets a child sign in and view balances, chores, and recommendation cards
- lets a parent review pending approvals and call outcomes
- lets an admin watch support calls, approval calls, and traces
- places outbound support calls through Bland
- places outbound parent approval calls through Bland
- stores support context in Ghost when available, with an in-memory fallback for local demo use
- supports Auth0 as the main auth path while preserving demo login for hackathon setups

## Product Flow

```text
child does chores
      |
      v
coins and balance go up
      |
      v
threshold is reached
      |
      v
Penny shows 3 recommendations
      |
      v
child asks for help / starts call
      |
      v
+-----------------------------+
|   Penny support call        |
|   Bland outbound calling    |
+-----------------------------+
      |
      v
question answered from:
  - profile data
  - recommendation data
  - knowledge articles
  - live LLM path
      |
      v
parent approval flow if needed
      |
      +------------------\
      |                   \
      v                    v
approved               declined
      |                    |
      v                    v
status updated        learning moment logged
```

## Architecture

```text
  +---------------------+        HTTP        +----------------------+
  | Next.js Frontend    | -----------------> | FastAPI Backend      |
  | child / parent /    | <----------------- | auth / dashboard /   |
  | admin dashboards    |                    | calls / webhooks     |
  +----------+----------+                    +----------+-----------+
             |                                            |
             |                                            |
             v                                            v
     +-------+--------+                           +-------+--------+
     | Auth0 or demo  |                           | Ghost or memory |
     | role-aware auth|                           | app data store  |
     +----------------+                           +-----------------+
                                                          |
                                                          |
                                                          v
                                              +-----------+-----------+
                                              | Bland voice platform  |
                                              | support + approval    |
                                              +-----------+-----------+
                                                          |
                                                          v
                                              +-----------+-----------+
                                              | NIM / LLM answer path |
                                              | grounded support      |
                                              +-----------------------+
```

## Main Screens

- `/login`
  Demo login plus Auth0-aware sign-in handling.
- `/dashboard`
  Child view with balance, ledger, recommendations, and support call trigger.
- `/parent`
  Parent view with pending approvals and approval call state.
- `/admin`
  Ops view with totals, recent calls, traces, and approval visibility.
- `/calls`
  Call session list and detail views.

Legacy routes like `/issues` and `/engineer` are still present for compatibility, but the active product experience is Penny customer care.

## Stack

### Frontend

- Next.js 16
- React 19
- TypeScript
- Tailwind CSS
- React Query
- Zustand
- Auth0 React SDK

### Backend

- FastAPI
- Pydantic
- `httpx`
- `python-jose`
- `psycopg`
- `structlog`
- `pytest`

### Integrations

- Auth0 for parent/child/admin identity
- Bland for support and approval calls
- Ghost/Postgres for runtime support data
- NVIDIA NIM for live-answer model access

## Repository Layout

```text
.
|-- backend/
|   |-- app/
|   |   |-- api/
|   |   |-- core/
|   |   |-- schemas/
|   |   |-- services/
|   |   |-- storage/
|   |   \-- tracing/
|   |-- tests/
|   |-- requirements.txt
|   \-- .env.example
|-- frontend/
|   |-- src/
|   |   |-- app/
|   |   |-- components/
|   |   |-- lib/
|   |   \-- store/
|   |-- package.json
|   \-- .env.example
|-- docs/
|-- shared/
\-- README.md
```

## Quick Start

### Prerequisites

- Python 3.9+
- Node.js 18+
- npm

### 1. Start the backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

Backend URLs:

- API: `http://localhost:8000`
- Docs: `http://localhost:8000/docs`

### 2. Start the frontend

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev
```

Frontend URL:

- App: `http://localhost:3000`

## Demo Accounts

The seeded demo flow uses these accounts:

- `maya@demo.com`
  Child experience
- `nina@demo.com`
  Parent experience
- `ops@demo.com`
  Admin / operations experience

## Important API Routes

### Auth and profile

- `POST /api/auth/demo-login`
- `GET /api/auth/status`
- `GET /api/auth/me`
- `GET /api/profile/me`
- `PATCH /api/profile/phone`

### Dashboard and recommendations

- `GET /api/dashboard`
- `GET /api/recommendations/current`

### Calls

- `POST /api/calls/support`
- `POST /api/calls/approval`
- `GET /api/calls`
- `GET /api/calls/{call_id}`

### Bland tool and webhook endpoints

- `POST /api/bland/tools/customer-context`
- `POST /api/bland/tools/answer-question`
- `POST /api/bland/tools/approval-decision`
- `POST /api/webhooks/bland/call`

### Observability

- `GET /api/health`
- `GET /api/traces`

## Environment Variables

See:

- [backend/.env.example](/Users/chetasparekh/Library/CloudStorage/OneDrive-SanFranciscoStateUniversity/Hackathons/27Mar%20AWS%20Hackathon/backend/.env.example)
- [frontend/.env.example](/Users/chetasparekh/Library/CloudStorage/OneDrive-SanFranciscoStateUniversity/Hackathons/27Mar%20AWS%20Hackathon/frontend/.env.example)

Important backend variables include:

- `APP_PUBLIC_URL`
- `GHOST_DATABASE_URL`
- `AUTH0_DOMAIN`
- `AUTH0_AUDIENCE`
- `AUTH0_MANAGEMENT_API_AUDIENCE`
- `BLAND_API_KEY`
- `BLAND_SUPPORT_VOICE_ID`
- `BLAND_APPROVAL_VOICE_ID`
- `NIM_BASE_URL`
- `NIM_API_KEY`
- `NIM_MODEL`

Important frontend variables include:

- `NEXT_PUBLIC_API_URL`
- `NEXT_PUBLIC_AUTH0_DOMAIN`
- `NEXT_PUBLIC_AUTH0_CLIENT_ID`
- `NEXT_PUBLIC_AUTH0_AUDIENCE`

## Local Development Notes

- If Ghost is unavailable or has the wrong schema, the backend falls back to memory storage during development.
- If Auth0 is not fully configured, demo login can still drive the child, parent, and admin flows.
- If the backend is only running on `localhost`, outbound Bland calls can still be queued in static mode, but live tool callbacks and webhook-driven grounded answers need a public HTTPS backend URL.

## Testing

Backend:

```bash
cd backend
source venv/bin/activate
pytest tests -q
```

Frontend:

```bash
cd frontend
npm run lint
```

## Current Status

What is solid today:

- seeded demo data
- role-aware dashboards
- support and approval call APIs
- call detail views
- Ghost or memory-backed storage
- Auth0-ready backend and frontend integration points

What still needs production hardening:

- final public deployment for live Bland tool/webhook callbacks
- stable production Ghost schema and migrations
- secret rotation and deployment-safe env handling
- full real-time grounded answer loop in a publicly reachable environment

## Why This Project Matters

Most finance products teach kids with charts after the fact. Penny tries to teach them in the moment, through conversation, encouragement, and real decisions with parents still in control.

The goal is not just to show a number on a dashboard. The goal is to make the first investing conversation feel understandable, friendly, and memorable.
