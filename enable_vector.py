from dotenv import load_dotenv
load_dotenv()
from app.db.session import engine
from sqlalchemy import text
conn = engine.connect()
conn.execute(text('CREATE EXTENSION IF NOT EXISTS vector'))
conn.commit()
print('Done')
