from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from src.core.config import settings

# pool_pre_ping=True checks if the connection is alive before routing the query
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)

sessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


# DI function to get the database session per request
def get_db():
    db = sessionLocal()
    try:
        yield db
    finally:
        db.close()
