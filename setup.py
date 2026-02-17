from app.db.session import engine
import sqlalchemy as sa
from datetime import datetime, timezone

now = datetime.now(timezone.utc)

with engine.connect() as conn:
    conn.execute(sa.text(
        "ALTER TABLE channels ADD COLUMN IF NOT EXISTS teacher_only BOOLEAN NOT NULL DEFAULT FALSE"
    ))
    conn.commit()
    print("column ready")

    existing = {r[0] for r in conn.execute(sa.text("SELECT name FROM channels")).fetchall()}
    print("existing:", existing)

    if "general" not in existing:
        conn.execute(sa.text("""
            INSERT INTO channels (id, name, slug, description, icon, channel_type, is_active, teacher_only, created_at)
            VALUES (gen_random_uuid(), 'general', 'general', 'General discussion', '💬', 'general', TRUE, FALSE, :now)
        """), {"now": now})
        print("created #general")

    if "offers" not in existing:
        conn.execute(sa.text("""
            INSERT INTO channels (id, name, slug, description, icon, channel_type, is_active, teacher_only, created_at)
            VALUES (gen_random_uuid(), 'offers', 'offers', 'Teacher offers', '🏷️', 'general', TRUE, TRUE, :now)
        """), {"now": now})
        print("created #offers")

    conn.commit()

    rows = conn.execute(sa.text("SELECT name, teacher_only FROM channels")).fetchall()
    print("final channels:", rows)