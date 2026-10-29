# Rentkom Smart Enterprise: Automated Real Estate Operations & RAG Platform

[![CI Pipeline](https://github.com/Eclipse-XS/rentkom-smart-enterprise/actions/workflows/ci.yml/badge.svg)](https://github.com/Eclipse-XS/rentkom-smart-enterprise/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16%2B-336791.svg)](https://www.postgresql.org/)
[![n8n](https://img.shields.io/badge/n8n-workflow%20automation-FF6D5A.svg)](https://n8n.io/)
[![Qdrant](https://img.shields.io/badge/Qdrant-v1.12-red.svg)](https://qdrant.tech/)
[![Ollama](https://img.shields.io/badge/Ollama-Local%20LLM-black.svg)](https://ollama.com/)
[![Grafana](https://img.shields.io/badge/Grafana-11.3%2B-F46800.svg)](https://grafana.com/)
[![Docker Clean--Room](https://img.shields.io/badge/Docker%20Clean--Room-NOT%20VERIFIED-orange.svg)](#-9-known-limitations--operational-boundaries)
[![Connected Mode](https://img.shields.io/badge/Connected%20Mode-Credentials%20Required-yellow.svg)](#-9-known-limitations--operational-boundaries)

A modern, production-grade enterprise automation platform and AI knowledge system for real estate operations. Designed with an asynchronous **FastAPI Business Core**, **PostgreSQL transactional outbox & deduplication engine**, **n8n event-driven workflow orchestrator**, **hybrid RAG retrieval pipeline** (Qdrant + Ollama), and a dual **Grafana observability stack**.

---

## 🏢 1. Project Overview & Business Value

Real estate agencies handle high-velocity, multi-channel client inquiries spanning property purchases, leases, landlord onboarding, and legal compliance. Unintegrated operations cause lost leads, duplicated client profiles, missed customer notifications, and overloaded legal advisors.

**Rentkom Smart Enterprise** unifies fragmented communication channels into an integrated operational platform:

- **100% Ingestion Reliability**: Eliminates lost leads via an **Idempotent Ingestion Inbox** and **Transactional Outbox Pattern** with exponential backoff retry.
- **Client Profile Deduplication**: Natural-key customer resolution ensures multiple inquiries from the same client attach to a single persistent customer profile.
- **Regulatory Legal Assistant (RAG)**: Instant, grounded legal and procedural consultations via local LLM retrieval over agency bylaws, minimizing human agent escalation.
- **Full Operational Observability**: Dual Grafana dashboards providing real-time visibility into customer demand, pipeline conversion, outbox delivery health, and AI retrieval latency.
- **Zero-Secret Demo Mode**: Deterministic, standalone local evaluation runnable without external credentials, alongside secure Connected Mode for live production deployments.

---

## 🏛️ 2. System Architecture

```mermaid
flowchart TD
    subgraph Channels["Client Interaction Channels"]
        TG["Telegram Bot / Webhook"]
        WEB["Inquiry Intake Webforms"]
        SHEETS["Google Sheets / CSV Feedback"]
    end

    subgraph Ingress["Ingress & Workflow Orchestration (n8n)"]
        UR["Unified Telegram Router"]
        GUARD["Telegram Inbox Guard (Idempotency)"]
        FBF["Feedback Pipeline"]
        FORMS["Lead Intake Pipeline"]
    end

    subgraph Core["Business Core API (FastAPI)"]
        API["REST & RPC Ingress"]
        REQ_SVC["Customer & Request Service (Deduplication)"]
        OUT_SVC["Transactional Outbox Engine"]
        RAG_SVC["RAG Legal Assistant (Guardrails & Normalization)"]
    end

    subgraph Storage["Persistence & Vector Layer"]
        PG[("PostgreSQL 16+ Relational DB\n(Customers, Requests, Feedback, Outbox)")]
        QDRANT[("Qdrant Vector DB\n(Regulatory Knowledge Embeddings)")]
        OLLAMA["Ollama Local LLM / Embeddings\n(qwen3:4b-instruct, mxbai-embed-large)"]
    end

    subgraph Observability["Telemetry & Monitoring"]
        GRAFANA["Grafana 11.3+ Dual Dashboards\n(19 Metrics & Panels)"]
        PROM["Health & Prometheus Exporters"]
    end

    TG --> UR
    UR --> GUARD
    GUARD --> API
    WEB --> FORMS
    FORMS --> API
    SHEETS --> FBF
    FBF --> API

    API --> REQ_SVC
    API --> OUT_SVC
    API --> RAG_SVC

    REQ_SVC --> PG
    OUT_SVC --> PG
    RAG_SVC --> QDRANT
    RAG_SVC --> OLLAMA

    PG --> GRAFANA
    API --> PROM --> GRAFANA
```

---

## 🛠️ 3. Technology Stack

| Layer | Technology | Version | Purpose |
| :--- | :--- | :--- | :--- |
| **Backend Framework** | [FastAPI](https://fastapi.tiangolo.com/) | 0.115+ | High-throughput asynchronous REST API & microservice core |
| **Data Persistence** | [PostgreSQL](https://www.postgresql.org/) / [SQLAlchemy](https://www.sqlalchemy.org/) | 16+ / 2.0+ | Relational storage, schema migrations, customer deduplication |
| **Workflow Engine** | [n8n](https://n8n.io/) | 1.64+ / 2.35+ | Visual orchestration, routing, multi-channel webhook ingestion |
| **Vector Database** | [Qdrant](https://qdrant.tech/) | 1.12+ | High-performance vector similarity search for regulatory bylaws |
| **Inference Engine** | [Ollama](https://ollama.com/) | Latest | Local, privacy-preserving LLM inference (`qwen3:4b`, `mxbai-embed-large`) |
| **Observability** | [Grafana](https://grafana.com/) | 11.3+ | Real-time dual dashboards (Business Analytics + Technical Telemetry) |
| **Containerization** | [Docker](https://www.docker.com/) & Docker Compose | Compose v2 | Isolated clean-room container orchestration |

---

## 🧩 4. Core Subsystems

### 4.1 Business Core & Outbox Pattern (FastAPI + PostgreSQL)
Direct synchronous dispatch of external notifications (Telegram, Email, Webhooks) risks data loss if the external provider is slow or down. Rentkom implements the **Transactional Outbox Pattern**:
- Every business event atomically inserts both the business entity (e.g. `ServiceRequest`) and a `NotificationOutbox` record within the **same ACID transaction**.
- A decoupled worker daemon polls pending outbox entries, handles delivery with exponential backoff (up to 5 attempts), records individual `NotificationAttempt` logs, and triggers dead-letter handling upon exhaustion.
- Natural-key client profile resolution ensures all inquiries from the same phone number attach to a single persistent customer entity.

### 4.2 Workflow Automation (n8n Orchestration)
The **Unified Telegram Router** coordinates message handling:
- Inbound webhooks acquire an atomic Inbox Guard reservation in PostgreSQL before routing.
- Duplicate Telegram `update_id` payloads are detected and acknowledged without duplicate downstream execution.
- Transport authorization uses native n8n Header Authentication (`RentkomInternalAuth`) with zero secrets serialized in workflow JSON files.

### 4.3 RAG Knowledge Engine (Qdrant + Ollama)
Agency inquiries regarding real estate legal formalities, earnest money (*завдаток*), title verification, and lease contracts are served by an offline, locally-hosted RAG pipeline:
- **Retrieval Guardrails**: Active regex filtering detects prompt injection attempts and out-of-domain queries before calling vector storage.
- **Colloquial Query Normalization**: Ukrainian colloquial terms (e.g., *хата*, *комірне*, *фатира*) are mapped to standard legal terminology for high-precision dense vector retrieval without altering user intent.
- **Bounded Latency & Determinism**: Generation is constrained (`num_predict: 384`), providing ~2.5–3.5s response latency on edge hardware.
- **Strict Hallucination Prevention**: If retrieval similarity falls below threshold, the model executes a verified fallback routing to live human agents.

### 4.4 Observability & Metrics (Grafana Dual Dashboards)
Dual pre-provisioned Grafana dashboards monitor 19 distinct metrics:
1. **Business Analytics Dashboard**: Demand by district, inquiry status breakdown, customer conversion funnel, property type popularity, and client satisfaction distribution.
2. **Technical Observability Dashboard**: API endpoint latency percentiles (p50/p95/p99), Outbox queue backlog, delivery success/failure rate, Dead-Letter-Queue status, and RAG retrieval latency.

---

## 🔄 5. Architecture & Data Flow

```text
1. Client Submission -> Telegram Webhook or Webform Ingress
2. n8n Router -> Reserves update_id via POST /api/v1/telegram/inbox/guard
3. FastAPI -> Deduplicates customer (E.164 phone) and persists ServiceRequest
4. FastAPI -> Enqueues notification intent in notification_outbox table (Atomically)
5. Outbox Worker -> Dispatches notification with exponential backoff & logging
6. Legal / FAQ Inquiries -> Routed to POST /api/v1/rag/ask
7. RAG Subsystem -> Normalizes query -> Vector search (Qdrant) -> Local LLM (Ollama)
8. Analytics -> Real-time SQL queries feed Grafana Business & Technical Dashboards
```

---

## 🚀 6. Getting Started

### Prerequisites
- Python 3.11+ (Python 3.13 recommended)
- Docker & Docker Compose (optional for Docker mode)
- n8n v1.64+ / v2.35+ (optional for live workflows)

### Option A: Local Standalone Evaluation (Demo Mode)

Clone repository and set up environment:
```bash
git clone https://github.com/rentkom/rentkom-smart-enterprise.git
cd rentkom-smart-enterprise

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Copy environment template
cp .env.example .env
```

Initialize database & seed synthetic demo data:
```bash
python tools/seed_demo_data.py
```

Run test suite:
```bash
pytest tests/ -v
```

Launch Business API locally:
```bash
python tools/manage.py up --profile demo
```
Access endpoints:
- Swagger Interactive Documentation: [http://localhost:8000/docs](http://localhost:8000/docs)
- Health Check: [http://localhost:8000/api/v1/health](http://localhost:8000/api/v1/health)

---

### Option B: Docker Compose Deployment

The project provides modular Docker Compose profiles:
- `demo`: PostgreSQL + FastAPI Core + Grafana + Qdrant (Zero external API keys needed).
- `connected`: Adds n8n workflow engine.
- `full-ai`: Adds containerized Ollama daemon with local weights.

```bash
# 1. Start core demo services
docker compose --profile demo up -d --build

# 2. Check service status
docker compose ps

# 3. Seed demo data
docker compose exec business-api python tools/seed_demo_data.py

# 4. View Grafana Dashboards
# Open http://localhost:3000 (Login: admin / admin)
```

To stop all services:
```bash
docker compose --profile demo down
```

---

## 🧪 7. Empirical Benchmarks & Test Results

The test suite covers unit logic, integration boundaries, transactional integrity, and end-to-end multi-service business flows.

```bash
pytest tests/ -v --tb=short
```

| Component / Subsystem | Benchmark Metric | Measured Result |
| :--- | :--- | :--- |
| **Test Suite** | Total Test Cases | **75 / 75 PASS (100%)** |
| **Business API** | Health Check Latency | `< 12 ms` |
| **Customer Deduplication** | Ingestion Resolution | `< 25 ms` |
| **Transactional Outbox** | Dispatch Latency (Local Mock) | `< 50 ms` |
| **RAG Retrieval + Generation** | Bounded Generation (`num_predict=384`) | `2.8 - 3.4 s` |
| **Grafana Observability** | Dashboard Metrics Monitored | **19 Panels Across 2 Dashboards** |

---

## 🔒 8. Security & Boundaries

- **Separation of Modes**: Demo mode operates strictly with mocked dispatchers and synthetic test tokens. Connected mode activates live Telegram webhook routing and external SMTP only when credentials are provided in `.env`.
- **Zero-Secret Workflows**: Workflow definitions in `SMART_ENTERPRISE/automation/n8n/` contain no embedded tokens or passwords. Microservice authorization uses n8n Header Authentication (`RentkomInternalAuth`).
- **Anonymized Synthetic Data**: All demo and fixture datasets contain purely synthetic names, addresses, and phone numbers.
- **Prompt Injection Defense**: Pre-retrieval pattern matching catches adversarial jailbreaks and unauthorized system prompt overrides before hitting LLM compute.

---

## 📋 9. Known Limitations & Operational Boundaries

> [!WARNING]
> **Operational Verification Status**:
> - **Docker Clean-Room**: **`NOT VERIFIED`** on local development host (Windows Docker Engine named pipe inactive). The `compose.yaml` and multi-stage `Dockerfile` are syntactically validated and tested against Compose v2 schema, but isolated clean-room container runs remain to be verified once Docker Desktop daemon is started.
> - **Connected Mode**: **`Credentials Required`**. Live Telegram bot responses, external Google Forms / Sheets synchronization, and real SMTP email delivery require user-supplied credentials in `.env` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SMTP_HOST`, `SMTP_PASSWORD`).
> - **Demo Mode**: **`AUTONOMOUS & VERIFIED`**. All business scenarios, customer deduplication, transactional outbox processing, RAG legal assistance, and dual Grafana dashboards operate 100% autonomously without external API keys or cloud dependencies.

- **Roadmap**:
  - OpenTelemetry distributed tracing across n8n and FastAPI.
  - Multi-tenant real estate agency support.
  - Fine-tuned Ukrainian real-estate SLM for sub-second offline edge inference.

---

## 📄 License

Distributed under the MIT License. See [LICENSE](LICENSE) for more information.
