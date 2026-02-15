# app/db/base.py
# Alembic model registry — imports Base + every model so Alembic detects all tables.
# Do NOT import this file from model files (use app.db.base_class instead).
# This file is only imported by:
#   - alembic/env.py        (schema detection)
#   - app/db/init_db.py     (seeding)

from app.db.base_class import Base  # noqa: F401

# ── Import all models here so Alembic can detect them ────────────────────────
# Order matters: parent tables before child tables (foreign key dependencies)
# This file is imported by alembic/env.py — do NOT import app logic here

from app.models.user import User, RefreshToken                          # noqa: F401, E402
from app.models.subscription import Plan, Subscription, Payment        # noqa: F401, E402
from app.models.teacher import (                                        # noqa: F401, E402
    TeacherProfile,
    TeacherVerification,
    VerificationDocument,
    TopPerformer,
)
from app.models.student import StudentProfile, Enrollment, Batch       # noqa: F401, E402
from app.models.class_ import Class, Attendance                    # noqa: F401, E402
from app.models.transcript import Transcript                           # noqa: F401, E402
from app.models.note import Note                                       # noqa: F401, E402
from app.models.assessment import (                                    # noqa: F401, E402
    StudentAssessment,
    StudentUnderstandingProfile,
)
from app.models.community import (                                     # noqa: F401, E402
    Channel,
    Post,
    Reply,
    Reaction,
)
from app.models.notification import Notification                       # noqa: F401, E402
from app.models.ai import ContentEmbedding, TutorSession               # noqa: F401, E402