from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
import os
from dotenv import load_dotenv

load_dotenv()

# Database connection string
DB_USER = os.getenv("POSTGRES_USER", "neurolibre")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD")
DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
DB_PORT = os.getenv("POSTGRES_PORT", "5432")
DB_NAME = os.getenv("POSTGRES_DB", "neurolibre")

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# Create engine with connection pooling
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,  # Verify connections before using them
    pool_recycle=3600,   # Recycle connections after 1 hour
    pool_size=10,        # Maximum number of connections to keep
    max_overflow=20      # Maximum number of connections to create beyond pool_size
)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create a scoped session for thread safety
ScopedSession = scoped_session(SessionLocal)

def get_db():
    """
    Dependency to get DB session with automatic cleanup.
    Use as: db = next(get_db())
    """
    db = ScopedSession()
    try:
        yield db
    finally:
        db.close()

def get_db_context():
    """
    Context manager for database sessions.
    Use as: with get_db_context() as db:
    """
    db = ScopedSession()
    try:
        yield db
    finally:
        db.close() 