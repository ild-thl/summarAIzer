# SummarAIzer v2

AI-powered content processing system that transforms session transcriptions into comprehensive summaries, tags, key takeaways, visual diagrams, and AI-generated images from conference and event recordings.

## ⚡ Features

- Automatic content generation from transcriptions
- Multi-format output (text, tags, diagrams, images)
- LLM-powered workflow with LangChain + OpenAI
- Asynchronous task processing via Celery
- S3-compatible cloud storage with public access
- Full REST API with OpenAPI docs

## 📊 Core Concepts

**Events** - Represent conferences, festivals, or other gatherings with multiple sessions.

**Sessions** - Individual talks, workshops, or presentations within an event.

**Content Workflows** - Pipeline that processes transcriptions to generate summaries, tags, key takeaways, diagrams, and images.

---

## 🚀 Quick Start

### Prerequisites
- Python 3.11+, PostgreSQL 14+, Redis 6+

Services: API (7860), Worker, PostgreSQL, Redis

### Local Development

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
alembic upgrade head

# Terminal 1: API
uvicorn main:app --reload

# Terminal 2: Worker
celery -A app.async_jobs.celery_app worker --loglevel=info
```

## 🔒 Authentication

All mutation endpoints (POST, PATCH, DELETE) require API key authentication via Bearer token:

```bash
curl -H "Authorization: Bearer YOUR_API_KEY" \
  -X POST http://localhost:7860/api/v2/events \
  -H "Content-Type: application/json" \
  -d '{"title": "My Event", "uri": "my-event", ...}'
```

**Authorization Model:**
- Create endpoints require authentication (user context)
- Update/Delete endpoints require resource ownership
- Unauthorized access returns `403 Forbidden`
- Missing/invalid auth returns `401 Unauthorized`

---

## 📡 API Endpoints

### Events
```
POST   /api/v2/events              Create event (requires auth)
GET    /api/v2/events              List events
GET    /api/v2/events/{id}         Get by ID
GET    /api/v2/events/by-uri/{uri} Get by URI
PATCH  /api/v2/events/{id}         Update (owner only)
DELETE /api/v2/events/{id}         Delete (owner only)
```

### Sessions
```
POST   /api/v2/sessions                    Create session (requires auth)
GET    /api/v2/sessions                    List (with filters)
GET    /api/v2/sessions/{id}               Get details
GET    /api/v2/sessions/by-uri/{uri}       Get by URI
PATCH  /api/v2/sessions/{id}               Update (owner only)
DELETE /api/v2/sessions/{id}               Delete (owner only)
```

### Content Management
```
GET    /api/v2/sessions/{id}/content                      Get available content
POST   /api/v2/sessions/{id}/content/transcription        Add transcription (owner only)
GET    /api/v2/sessions/{id}/content/{identifier}         Get content by ID
PATCH  /api/v2/sessions/{id}/content/{identifier}         Update content (owner only)
DELETE /api/v2/sessions/{id}/content/{identifier}         Delete content (owner only)
```

### Workflows
```
POST   /api/v2/sessions/{id}/workflow/{target}           Trigger generation (owner only)
  target: 'talk_workflow' (all steps) or individual steps
GET    /api/v2/sessions/{id}/workflow/{execution_id}     Check job status
```

Full OpenAPI documentation at `/docs` when running.

## 🧪 Testing

```bash
# All tests
pytest tests/ -x

# With coverage
pytest tests/ --cov=app --cov-report=term-missing

# Specific file
pytest tests/test_content_workflow.py -v
```

---

## 📐 Architecture

The app uses a layered architecture with comprehensive authorization:

- **Routes** - REST API endpoints with authentication (FastAPI)
- **Security** - JWT/API key validation and ownership verification
- **CRUD** - Database operations (SQLAlchemy)
- **Schemas** - Request/response models (Pydantic)
- **Workflows** - Content generation pipelines (LangGraph)
- **Services** - LLM and storage integration
- **Async Jobs** - Background task queue (Celery)

### Workflow Execution
When a workflow is triggered:
1. Ownership verified (user must own session)
2. Task queued to Redis
3. Celery worker picks up task
4. Pipeline executes steps: summary → tags → takeaways → diagram → image
5. Results stored in database and S3
6. Status queryable via API

## 🏗️ Project Layout

```
backend/
├── app/
│   ├── async_jobs/      Celery tasks & queuing
│   ├── config/          Settings & environment
│   ├── crud/            Database CRUD operations
│   ├── database/        SQLAlchemy models
│   ├── routes/          API endpoints
│   ├── schemas/         Pydantic models
│   ├── security/        JWT authentication
│   ├── utils/           Helper functions
│   └── workflows/       LangGraph pipeline definitions
├── tests/               168+ test suite
├── alembic/             Database migrations
├── main.py              FastAPI app entry point
└── requirements.txt     Dependencies
```

## 📚 Stack & Dependencies

- **FastAPI** - Web framework
- **SQLAlchemy** - ORM
- **PostgreSQL** - Database
- **Celery + Redis** - Async task queue
- **LangChain** - LLM orchestration
- **LangGraph** - Workflow DAG
- **OpenAI** - Language models
- **boto3** - S3 storage
- **pytest** - Testing framework (168 tests)

---

## 🔒 Security

**Authorization Model:**
- Every authenticated user can create events and sessions
- Only resource owners can modify or delete their resources
- Unauthorized access attempts return 403 Forbidden
- All mutation endpoints require valid API key authentication

---

## 🚀 Next Steps

- **Dynamic workflows** - Workflows adapt based on session format (quotes, workshops, talks)
- **Transcription generation** - Auto-generate transcriptions from media
- **Frontend UI** - Event/session management and content viewing

---

## 📄 License

Part of the ISy/DLC project suite.

**Version:** 2.0.0
