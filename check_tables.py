from dotenv import load_dotenv
load_dotenv()
from app.db.session import engine
from sqlalchemy import text
conn = engine.connect()
result = conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")).fetchall()
print(result)
