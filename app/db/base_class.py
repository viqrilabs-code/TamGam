# app/db/base_class.py
# Contains ONLY the SQLAlchemy declarative Base.
# All model files import from here to avoid circular imports.
# app/db/base.py (the Alembic registry) imports from here + all models.

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """
    Single declarative base for all TamGam ORM models.
    """
    pass