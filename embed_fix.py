from app.db.session import SessionLocal
from app.services.gemini_key_manager import generate_embedding_with_fallback
from sqlalchemy import text

db = SessionLocal()
rows = db.execute(text("SELECT id, chunk_text FROM content_embeddings WHERE embedding IS NULL")).fetchall()
print(f"Found {len(rows)} chunks without embeddings")

success = 0
for i, row in enumerate(rows):
    try:
        emb = generate_embedding_with_fallback(row.chunk_text)
        if emb:
            emb = emb[:768]
            emb_str = "[" + ",".join(str(v) for v in emb) + "]"
            db.execute(
                text("UPDATE content_embeddings SET embedding = cast(:emb as vector) WHERE id = cast(:id as uuid)"),
                {"emb": emb_str, "id": str(row.id)}
            )
            success += 1
        if (i+1) % 20 == 0:
            db.commit()
            print(f"Progress: {i+1}/{len(rows)}, success: {success}")
    except Exception as e:
        db.rollback()
        print(f"Chunk {i} failed: {e}")

db.commit()
print(f"Done: {success}/{len(rows)} embedded")
db.close()
