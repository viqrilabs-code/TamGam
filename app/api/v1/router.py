# app/api/v1/router.py
# Master router -- registers all endpoint routers under /api/v1
# Each endpoint module registers its own router with its own prefix and tags

from fastapi import APIRouter

# Import all endpoint routers
# These are empty stubs for now -- filled in subsequent components
from app.api.v1.endpoints import (
    auth,
    users,
    teachers,
    students,
    subscriptions,
    classes,
    transcripts,
    notes,
    assessments,
    tutor,
    community,
    channels,
    notifications,
    admin,
)
from app.api.v1.endpoints import tuition_requests
from app.api.v1.endpoints.admin_books import router as books_router

api_router = APIRouter()

# Auth
api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])

# Users
api_router.include_router(users.router, prefix="/users", tags=["Users"])

# Teachers
api_router.include_router(teachers.router, prefix="/teachers", tags=["Teachers"])

# Students
api_router.include_router(students.router, prefix="/students", tags=["Students"])

# Subscriptions & Payments
api_router.include_router(subscriptions.router, prefix="/subscriptions", tags=["Subscriptions"])

# Classes & Content
api_router.include_router(classes.router, prefix="/classes", tags=["Classes"])
api_router.include_router(transcripts.router, prefix="/transcripts", tags=["Transcripts"])
api_router.include_router(notes.router, prefix="/notes", tags=["Notes"])
api_router.include_router(assessments.router, prefix="/assessments", tags=["Assessments"])

# AI Tutor
api_router.include_router(tutor.router, prefix="/tutor", tags=["AI Tutor - Diya"])

# Community
api_router.include_router(channels.router, prefix="/channels", tags=["Community - Channels"])
api_router.include_router(community.router, prefix="/posts", tags=["Community - Posts"])

# Notifications
api_router.include_router(notifications.router, prefix="/notifications", tags=["Notifications"])

# Admin
api_router.include_router(admin.router, prefix="/admin", tags=["Admin"])

# Tuition Requests
api_router.include_router(tuition_requests.router, prefix="/tuition-requests", tags=["Tuition Requests"])

# Admin Books (Content Embeddings)
api_router.include_router(books_router, prefix="/admin/books", tags=["Admin Books"])