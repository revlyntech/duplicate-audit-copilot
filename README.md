# Duplicate Audit Copilot

An intelligent duplicate detection and merge assistant for HubSpot CRM. The application scans HubSpot contacts, identifies duplicate records using a combination of fuzzy matching and AI validation, and surfaces safe, one-click merge suggestions directly inside HubSpot through a native CRM Card built on the HubSpot Projects framework.

## Overview

Duplicate Audit Copilot helps RevOps and sales teams maintain a clean CRM by automatically detecting duplicate contacts that traditional rule-based systems miss. It uses fuzzy string matching for fast initial scanning, then validates borderline cases with Claude AI to reduce false positives. Results appear inside HubSpot as a CRM Card, allowing users to review clusters, see match reasons, and merge records without leaving the platform.

## Features

- Native HubSpot CRM Card built with the HubSpot UI Extensions framework
- HubSpot OAuth integration for secure portal connection
- Asynchronous job processing through a Redis-backed queue
- Multi-signal duplicate detection using email, phone, name, and company
- AI-powered validation for borderline matches using Claude
- Union-Find clustering to group multiple duplicates of the same contact
- Automatic master record selection based on data completeness
- Confidence scoring for every duplicate cluster
- One-click merge through the native HubSpot merge API
- Human-readable match reasons for every detected duplicate

## Architecture

The application consists of four core components working together:

1. **API Server (`main.py`)** — A FastAPI service that handles OAuth, job creation, status polling, and merge requests from the HubSpot CRM Card.
2. **Background Worker (`worker.py`)** — A long-running process that pulls jobs from the Redis queue, fetches contacts from HubSpot, and runs the duplicate detection pipeline.
3. **Detection Engine (`logic.py`)** — The matching pipeline, including blocking, scoring, AI validation, clustering, and merge suggestion generation.
4. **HubSpot UI Extension (`src/app/cards/`)** — A CRM Card built on the HubSpot Projects framework (platform version 2026.03) that displays duplicate clusters and merge controls inside HubSpot.

## Tech Stack

### Backend
- **Framework:** FastAPI
- **Queue and State Store:** Upstash Redis
- **CRM Integration:** HubSpot CRM API v3
- **AI Validation:** Anthropic Claude API
- **Fuzzy Matching:** RapidFuzz
- **Runtime:** Python 3.10+

### Frontend
- **Platform:** HubSpot Projects, platform version 2026.03
- **UI Library:** `@hubspot/ui-extensions`
- **HTTP Client:** `hubspot.fetch` (HubSpot-managed fetch API)
- **CLI:** HubSpot CLI (`hs`)

## Project Structure

```
duplicate-audit-copilot/
├── hsproject.json              # HubSpot project configuration
├── src/                        # HubSpot project source directory
│   └── app/
│       ├── app-hsmeta.json     # App component configuration
│       └── cards/              # CRM Card UI extensions
│           ├── DuplicateCard.jsx
│           └── duplicate-card-hsmeta.json
├── main.py                     # FastAPI application (OAuth, jobs, merge endpoints)
├── worker.py                   # Background job processor
├── logic.py                    # Duplicate detection and clustering logic
├── requirements.txt            # Python dependencies
├── .env                        # Environment variables (not committed)
└── README.md
```

## Prerequisites

Before running the application, ensure you have the following:

- Python 3.10 or higher
- Node.js 18 or higher (required by the HubSpot CLI)
- HubSpot CLI installed globally (`npm install -g @hubspot/cli`)
- A HubSpot developer account with an app created in the Projects framework
- An Upstash Redis database
- An Anthropic API key for Claude

## Backend Setup

### 1. Install dependencies

```bash
git clone https://github.com/your-username/duplicate-audit-copilot.git
cd duplicate-audit-copilot
pip install -r requirements.txt
```

### 2. Configure environment variables

Create a `.env` file in the project root:

```
HUBSPOT_API_KEY=your_hubspot_private_app_token
HUBSPOT_CLIENT_ID=your_oauth_client_id
HUBSPOT_CLIENT_SECRET=your_oauth_client_secret
HUBSPOT_REDIRECT_URI=http://localhost:8000/oauth/callback

CLAUDE_API_KEY=your_anthropic_api_key

UPSTASH_REDIS_REST_URL=your_upstash_url
UPSTASH_REDIS_REST_TOKEN=your_upstash_token
```

### 3. Run the API server

```bash
uvicorn main:app --reload --port 8000
```

### 4. Run the background worker

In a separate terminal:

```bash
python worker.py
```

The API will be available at `http://localhost:8000`.

## Frontend Setup (HubSpot UI Extension)

The frontend is a HubSpot CRM Card built on the HubSpot Projects framework. It lives inside the `src/` directory, defined by `hsproject.json`.

### Project Configuration

The `hsproject.json` file declares the project name, source directory, and platform version:

```json
{
  "name": "Duplicate Audit",
  "srcDir": "src",
  "platformVersion": "2026.03"
}
```

### Authenticate the HubSpot CLI

If this is your first time using the HubSpot CLI on this machine, run:

```bash
hs init
hs account auth
```

This opens a browser window to authenticate against your HubSpot account.

### Permitted URLs

Because UI extensions cannot use the browser `window.fetch`, all backend calls go through `hubspot.fetch`, which requires every destination URL to be listed in the app's `*-hsmeta.json` file:

```json
"permittedUrls": {
  "fetch": [
    "https://your-backend-domain.com",
    "https://api.hubapi.com"
  ],
  "iframe": [],
  "img": []
}
```

All fetch URLs must use HTTPS. Plain `localhost` URLs are not allowed at runtime.

### Local Development

Start a local HubSpot development server from the project root:

```bash
hs project dev
```

This syncs UI extension changes to HubSpot in real time. While the server is running, the card displays a "Developing locally" tag inside HubSpot, and saving JSX files automatically refreshes the card.

### Proxying to a Local Backend

During development, the card needs to talk to the FastAPI server running on `http://localhost:8000`. Since `hubspot.fetch` does not accept localhost URLs, create a `local.json` file in the same directory as the app's `*-hsmeta.json`:

```json
{
  "proxy": {
    "https://your-backend-domain.com": "http://localhost:8000"
  }
}
```

The CLI confirms the proxy is active when `hs project dev` starts. To disable it, rename the file to `local.json.bak` and restart the dev server.

### Deploying the Frontend

To upload and deploy the project to HubSpot:

```bash
hs project upload
hs project deploy
```

Use `hs project logs` to view runtime logs for the deployed extension.

## Backend API Endpoints

| Method | Endpoint            | Description                                       |
| ------ | ------------------- | ------------------------------------------------- |
| GET    | `/health`           | Health check endpoint                             |
| GET    | `/`                 | Root status page                                  |
| GET    | `/oauth/callback`   | HubSpot OAuth callback handler                    |
| POST   | `/start-job`        | Queue a new duplicate detection scan              |
| GET    | `/job/{job_id}`     | Retrieve job status and results                   |
| POST   | `/merge`            | Merge a duplicate contact into a primary contact  |

### Example: Starting a Scan

```bash
curl -X POST http://localhost:8000/start-job \
     -H "Content-Type: application/json" \
     -d '{}'
```

Response:

```json
{ "job_id": "abc-123", "status": "queued" }
```

### Example: Polling for Results

```bash
curl http://localhost:8000/job/abc-123
```

### Example: Merging Two Contacts

```bash
curl -X POST http://localhost:8000/merge \
     -H "Content-Type: application/json" \
     -d '{"primary_id": "101", "duplicate_id": "202"}'
```

## How Duplicate Detection Works

The detection pipeline runs in four stages:

**1. Blocking.** Records are grouped into candidate blocks based on email domain, the last seven digits of the phone number, and the first three characters of the name. This avoids the O(n squared) cost of comparing every record to every other record.

**2. Scoring.** Each candidate pair is scored using a weighted combination of signals:

- Exact email match contributes 0.90
- Matching phone number (last 7 digits) contributes 0.85
- Name similarity (token set ratio) contributes up to 0.60
- Company similarity (token set and partial ratio) contributes up to 0.50

Pairs scoring above 0.85 are treated as confirmed matches. Pairs between 0.60 and 0.85 are sent to Claude for validation.

**3. AI Validation.** Borderline pairs are batched into a single Claude prompt that returns a structured JSON verdict for each pair, reducing false positives without slowing the scan.

**4. Clustering and Master Selection.** Confirmed pairs are merged using a Union-Find structure to form clusters. The master record in each cluster is the one with the highest data completeness score, and a merge suggestion is generated by combining the best available field values.

## Confidence Scoring

Every cluster returns a confidence value between 0 and 1, computed as the average pairwise match score across all records in the cluster. This gives reviewers a clear signal of how strong a match is before they approve a merge.

## Useful HubSpot CLI Commands

| Command                | Description                                          |
| ---------------------- | ---------------------------------------------------- |
| `hs project dev`       | Start a local dev server for UI extensions           |
| `hs project upload`    | Upload the project to HubSpot                        |
| `hs project deploy`    | Deploy the latest build to make it live              |
| `hs project logs`      | View runtime logs for the deployed project           |
| `hs project validate`  | Validate project configuration files                 |
| `hs account list`      | List configured HubSpot accounts                     |
| `hs account use`       | Switch the default HubSpot account                   |

## Roadmap

- Support for duplicate detection on Companies and Deals
- Scheduled recurring scans
- Bulk merge approval workflow
- Webhook notifications for newly detected duplicates
- Audit log of all merge actions

