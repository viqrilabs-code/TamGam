from dotenv import load_dotenv
load_dotenv()
import app.db.base
from app.db.base_class import Base
from app.db.session import engine
Base.metadata.create_all(engine)
print('Done')
