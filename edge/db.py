"""
Edge Database Engine and Session Dependency
"""

from typing import Generator
from sqlalchemy.orm import sessionmaker, Session

from contract.db.models import Base
from contract.db.session import create_db_engine
from edge.config import DATABASE_URL

engine = create_db_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_edge_db():
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
