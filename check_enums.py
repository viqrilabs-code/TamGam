from dotenv import load_dotenv
load_dotenv()
from app.db.session import engine
from sqlalchemy import text
conn = engine.connect()
result = conn.execute(text("SELECT typname FROM pg_type WHERE typtype = 'e'")).fetchall()
print(result)
