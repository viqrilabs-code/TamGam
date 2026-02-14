# TamGam 🎓

> **AI-powered live teaching platform** — Live classes on Google Meet, AI-generated notes, adaptive assessments, level-aware AI tutor, and a thriving community. Built for the Indian EdTech market.

---

## What is TamGam?

TamGam is a subscription-based EdTech platform where teachers conduct live classes on **Google Meet**, and the platform handles everything else — capturing transcripts, generating AI notes, assessing student understanding, and providing a personalised AI tutor that explains concepts at exactly the right level for each student.

The platform does not build video infrastructure. Google Meet handles live classes. TamGam builds the intelligence layer on top.

---

## Core Value Proposition

| For Students | For Teachers |
|---|---|
| AI notes generated from every class | Zero setup — use Google Meet as always |
| Adaptive AI tutor, tuned to your level | Transcript automatically captured |
| 24/7 doubt solving grounded in class content | Student understanding dashboard post-class |
| Community with peers and teachers | See exactly where students struggled |
| Performance tracking across classes | Verified T mark builds credibility |

---

## Feature Overview

### 🎥 Live Classes
- Classes conducted on **Google Meet** (Google Workspace)
- Meet links gated to subscribed students only
- Transcripts automatically pulled from **Google Drive** via API after class ends
- No custom video infrastructure needed

### 📝 AI Notes Generation
- Transcript processed by **Gemini 2.5 Flash** (Vertex AI) via Celery-equivalent **Cloud Tasks**
- Generates: class summary, key points, detailed notes, Q&A pairs, topics covered
- Teacher reviews draft before publishing to students
- Students notified when notes are live

### 🎯 Adaptive Assessment System
- AI generates a **8–10 question test** after each class from the transcript
- Question distribution:
  - **~40%** from one standard **below** class level (confidence building)
  - **~40%** at the **actual** class level (core understanding)
  - **~20%** from one standard **above** (stretch goals)
- Student understanding scored on a **1–5 level scale**
- Level re-evaluated every 3 classes — moves up or down based on performance
- Teacher sees **aggregated class heatmap** of understanding levels

### 🤖 AI Tutor (Level-Aware RAG)
- Powered by **Retrieval Augmented Generation** on class transcripts (pgvector)
- Every answer grounded in actual class content — not generic internet knowledge
- System prompt tuned to student's **current understanding level (1–5)**
- Level 1: Patient, analogies, micro-steps, real-world examples
- Level 3: Standard explanations, worked examples, conceptual depth
- Level 5: Peer-level discussion, proofs, competitive exam depth
- Tracks weak areas per topic across classes
- Practice problem generator at the right difficulty

### 💬 Community (Open to All)
- **Slack-style** community with subject channels
- Open to read for **everyone** (no login required)
- Posting, replying, reacting requires **free account**
- **Pink Star ⭐** mark for subscribed students
- **Golden Circle 🟡 T** mark for document-verified teachers
- AI suggests similar past questions before posting (reduces duplicates)
- Teacher moderation tools per channel

### 👩‍🏫 Teacher Portal
- Public profile with bio, subjects, ratings
- **Top performing students** shown on public profile (public info only — no sensitive data)
- Document verification workflow for T mark (ID + certificates uploaded to private GCS bucket, admin reviewed)
- Post-class: student understanding distribution, weak area alerts, suggested revision topics
- Notes review and publish workflow
- Earnings dashboard (commission-based)

### 👨‍🎓 Student Portal
- Subscription management (Razorpay)
- Upcoming classes with Meet links (subscribed only)
- AI notes per class (published by teacher)
- AI Tutor chat interface
- Post-class assessments
- Performance dashboard across classes
- Public profile (name, avatar, badges, score — no sensitive fields)

### 🔐 Admin Portal
- Teacher verification queue (approve/reject documents)
- User and subscription management
- Revenue reports
- AI processing logs and error monitoring
- Community moderation queue

---

## Identity Marks

| Mark | Who | How Earned |
|---|---|---|
| ⭐ Pink Star with ✓ | Subscribed students | Active subscription |
| 🟡 Golden Circle with T | Verified teachers | Document verification approved by admin |

Marks are **computed at query time** from the database — never cached. Appear consistently across community feed, profiles, reply threads, top performers list, and @mention suggestions.

---

## Access Control Matrix

| Feature | Anonymous | Logged In (Free) | Subscribed ✅ | Verified Teacher 🟡 |
|---|---|---|---|---|
| Community — read | ✅ | ✅ | ✅ | ✅ |
| Community — post/reply/react | ❌ → signup nudge | ✅ | ✅ | ✅ |
| Live class Meet links | ❌ | ❌ | ✅ | ✅ |
| AI Notes | ❌ | ❌ | ✅ | ✅ |
| Adaptive Assessment | ❌ | ❌ | ✅ | ✅ |
| AI Tutor | ❌ | ❌ | ✅ | ✅ |
| Teacher dashboard | ❌ | ❌ | ❌ | ✅ |
| Admin portal | ❌ | ❌ | ❌ | Admin only |

---

## Tech Stack

### Backend
| Layer | Technology |
|---|---|
| Framework | **FastAPI** (Python 3.12) |
| Database | **Cloud SQL** — PostgreSQL 16 + pgvector |
| Cache / Pub-Sub | **Memorystore** (Redis) |
| Async Jobs | **Cloud Tasks** + **Cloud Run Jobs** |
| ORM | **SQLAlchemy 2.0** + Alembic |
| Auth | JWT (python-jose) + Google OAuth (authlib) |

### AI / ML
| Layer | Technology |
|---|---|
| Notes Generation | **Vertex AI — Gemini 2.5 Flash** |
| AI Tutor | **Vertex AI — Gemini 2.5 Flash** (RAG) |
| Assessment Generation | **Vertex AI — Gemini 2.5 Flash** |
| Embeddings (RAG) | **Gemini text-embedding-004** |
| Vector Store | **pgvector** (PostgreSQL extension) |

### Google Cloud Platform (asia-south1 — Mumbai)
| Service | Purpose |
|---|---|
| **Cloud Run** | FastAPI API hosting (serverless, scales to zero) |
| **Cloud SQL** | PostgreSQL managed database |
| **Memorystore** | Redis — WebSocket pub/sub, session cache |
| **Cloud Tasks** | Async job queue (transcript processing, AI notes) |
| **Cloud Run Jobs** | Background worker execution |
| **Vertex AI** | Gemini API for all AI features |
| **Cloud Storage** | Transcripts, verification docs (private), avatars |
| **Firebase Hosting** | Static frontend — global CDN |
| **Secret Manager** | All credentials and secrets |
| **Cloud Build** | CI/CD — auto deploy on git push |
| **Artifact Registry** | Docker image storage |
| **Cloud Logging** | Centralised logging and monitoring |

### Frontend
| Layer | Technology |
|---|---|
| Framework | Static HTML + **Alpine.js** (lightweight reactivity) |
| Styling | **Tailwind CSS** (CDN) |
| Hosting | **Firebase Hosting** (global CDN) |
| Real-time | WebSocket (FastAPI) |

### Payments
| Service | Purpose |
|---|---|
| **Razorpay** | Student subscriptions (India-first) |

### Third Party
| Service | Purpose |
|---|---|
| **Google Meet** | Live class video (Google Workspace Business Standard) |
| **Google Drive API** | Transcript pull post-class |
| **SendGrid** | Email notifications |

---

## Database Schema (Key Tables)

```
users                          # Base user — all roles
subscriptions                  # Student subscription + status
plans                          # Subscription plans
payments                       # Payment audit trail
teacher_profiles               # Teacher public + private data
teacher_verifications          # T mark verification workflow
verification_documents         # Uploaded docs (GCS private)
student_profiles               # Student public data + performance score
enrollments                    # Student ↔ Teacher relationships
batches                        # Student groups per teacher
top_performers                 # Cached rankings (Celery recomputed)
classes                        # Scheduled Google Meet sessions
attendances                    # Per-student attendance per class
transcripts                    # Raw transcript text from Drive
notes                          # AI-generated structured notes
content_embeddings             # pgvector — RAG for AI Tutor
tutor_sessions                 # AI Tutor conversation history
student_assessments            # Adaptive test results per class
student_understanding_profiles # Current level (1–5) per student/subject
channels                       # Community channels
posts                          # Community posts
replies                        # Threaded replies
reactions                      # Emoji reactions (posts + replies)
notifications                  # In-app notification queue
refresh_tokens                 # JWT refresh token rotation
```

---

## Pricing

### Student Plans

| Plan | Monthly | Annual | Includes |
|---|---|---|---|
| Free | ₹0 | ₹0 | Community read + post |
| Basic | ₹499 | ₹4,788 | 1 subject, AI notes, AI tutor, community ✅ |
| Standard | ₹999 | ₹9,588 | Up to 3 subjects, notes, tutor, Q&A |
| Pro | ₹1,499 | ₹14,388 | All subjects, priority support, downloads |

*Annual plans = 2 months free. All prices include 18% GST.*

### Teacher Plans

Teachers earn via **platform commission**:

| Monthly Revenue | Commission |
|---|---|
| ₹0 – ₹50,000 | 20% |
| ₹50,001 – ₹2,00,000 | 15% |
| ₹2,00,001+ | 10% |

Optional verified teacher subscription (₹299–₹799/month) for T mark + advanced features.

---

## Infrastructure Cost Estimates (asia-south1)

| Students | Monthly GCP Cost | Revenue (avg) | Net Profit |
|---|---|---|---|
| 0 (zero activity) | ~₹5,000 | ₹0 | -₹5,000 |
| 14 (break-even) | ~₹6,000 | ~₹6,000 | ₹0 |
| 50 | ~₹9,500 | ~₹31,000 | ~₹16,000 |
| 200 | ~₹22,000 | ~₹1,20,000 | ~₹80,000 |
| 500 | ~₹48,000 | ~₹3,00,000 | ~₹2,50,000 |

*Break-even at just 14 paying students.*

---

## Project Structure

```
tamgam/
├── app/
│   ├── main.py                    # FastAPI entry point
│   ├── api/v1/
│   │   ├── router.py              # Master API router
│   │   └── endpoints/
│   │       ├── auth.py            # Login, signup, Google OAuth
│   │       ├── users.py           # User profiles
│   │       ├── teachers.py        # Teacher profile + verification
│   │       ├── students.py        # Student profile + performance
│   │       ├── subscriptions.py   # Razorpay webhooks + plans
│   │       ├── classes.py         # Schedule + Meet links
│   │       ├── transcripts.py     # Drive webhook + storage
│   │       ├── notes.py           # AI notes review + publish
│   │       ├── assessments.py     # Adaptive tests
│   │       ├── tutor.py           # AI Tutor RAG endpoint
│   │       ├── community.py       # Posts, replies, reactions + WS
│   │       ├── channels.py        # Community channels
│   │       ├── notifications.py   # In-app notifications
│   │       └── admin.py           # Admin portal
│   ├── core/
│   │   ├── config.py              # Settings (GCP Secret Manager)
│   │   ├── security.py            # JWT + password hashing
│   │   ├── dependencies.py        # Auth guards + mark resolver
│   │   └── permissions.py         # RBAC
│   ├── db/
│   │   ├── base.py                # SQLAlchemy Base
│   │   ├── session.py             # Cloud SQL connection
│   │   └── init_db.py             # Seed data
│   ├── models/                    # SQLAlchemy ORM models
│   ├── schemas/                   # Pydantic request/response models
│   ├── services/
│   │   ├── google_drive.py        # Transcript pull
│   │   ├── vertex_ai.py           # Gemini API calls
│   │   ├── cloud_tasks.py         # Job enqueueing
│   │   ├── cloud_storage.py       # GCS uploads
│   │   ├── razorpay_service.py    # Payment processing
│   │   ├── community_service.py   # WebSocket manager
│   │   └── notification_service.py
│   └── jobs/                      # Cloud Run Jobs (workers)
│       ├── process_transcript.py
│       ├── generate_notes.py
│       ├── generate_assessment.py
│       ├── embed_content.py       # pgvector embedding
│       └── recompute_rankings.py
├── infra/
│   ├── terraform/                 # GCP infrastructure as code
│   └── cloudbuild.yaml            # CI/CD pipeline
├── static/                        # Frontend (Alpine.js)
├── tests/
├── alembic/                       # DB migrations
├── Dockerfile
├── Dockerfile.worker
├── docker-compose.yml             # Local development
├── requirements.txt
└── .env.example
```

---

## Local Development Setup

```bash
# 1. Clone the repository
git clone https://github.com/yourusername/tamgam.git
cd tamgam

# 2. Copy environment variables
cp .env.example .env
# Fill in your credentials in .env

# 3. Start all services
docker compose up -d

# 4. Run database migrations
docker compose exec api alembic upgrade head

# 5. Seed initial data
docker compose exec api python -m app.db.init_db

# 6. API is running at
http://localhost:8000

# 7. API docs (development only)
http://localhost:8000/api/docs
```

---

## Environment Variables

See `.env.example` for the full list. Key variables:

```bash
# GCP
GCP_PROJECT_ID=your-project-id
GCP_REGION=asia-south1

# Database (Cloud SQL)
DB_CONNECTION_NAME=project:region:instance
DB_NAME=tamgam

# Google OAuth
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...

# Google Drive (transcript pull)
GOOGLE_SERVICE_ACCOUNT_JSON=/secrets/sa.json
GOOGLE_DRIVE_FOLDER_ID=...

# Vertex AI
GEMINI_MODEL=gemini-2.5-flash

# Razorpay
RAZORPAY_KEY_ID=rzp_live_...
RAZORPAY_KEY_SECRET=...
```

---

## Deployment (GCP)

```bash
# Authenticate with GCP
gcloud auth login
gcloud config set project YOUR_PROJECT_ID

# Deploy via Cloud Build (triggered automatically on git push to main)
git push origin main

# Manual deploy if needed
gcloud run deploy tamgam-api \
  --source . \
  --region asia-south1 \
  --set-cloudsql-instances YOUR_CONNECTION_NAME
```

---

## API Overview

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/v1/auth/signup` | Create account |
| POST | `/api/v1/auth/login` | Get JWT token |
| GET | `/api/v1/auth/google` | Google OAuth |
| GET | `/api/v1/posts/{channel_id}` | List community posts (open) |
| POST | `/api/v1/posts/{channel_id}` | Create post (login required) |
| POST | `/api/v1/tutor/ask` | Ask AI Tutor (subscription required) |
| GET | `/api/v1/notes/{class_id}` | Get class notes (subscription required) |
| GET | `/api/v1/assessments/{class_id}` | Get assessment (subscription required) |
| POST | `/api/v1/assessments/{class_id}/submit` | Submit assessment |
| GET | `/api/v1/teachers/{id}/profile` | Teacher public profile |
| GET | `/api/v1/students/{id}/profile` | Student profile (public fields only) |
| POST | `/api/v1/subscriptions/webhook` | Razorpay webhook |
| WS | `/api/v1/posts/ws/{channel_id}` | Community real-time WebSocket |
| GET | `/api/v1/admin/verifications` | Teacher verification queue (admin) |

---

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/your-feature`)
3. Commit your changes (`git commit -m 'Add your feature'`)
4. Push to the branch (`git push origin feature/your-feature`)
5. Open a Pull Request

---

## Roadmap

- [ ] GCP infrastructure (Terraform)
- [ ] Auth system (JWT + Google OAuth)
- [ ] Student and Teacher portals
- [ ] Google Drive transcript pipeline
- [ ] AI Notes generation (Vertex AI)
- [ ] Adaptive Assessment system
- [ ] AI Tutor (RAG + pgvector)
- [ ] Community with WebSocket real-time
- [ ] Razorpay subscription integration
- [ ] Admin portal + Teacher verification
- [ ] Firebase Hosting frontend deploy
- [ ] Mobile responsive UI
- [ ] Push notifications (Firebase FCM)
- [ ] Certificate generation on course completion
- [ ] Multi-language support (Hindi, Marathi)

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

<div align="center">
  Built with ❤️ for Indian students and teachers
</div>
