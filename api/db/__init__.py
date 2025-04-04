from .models import Base, Submission, Task, ZenodoRecord, Asset, TaskStatus, AssetType
from .session import engine, get_db, get_db_context, ScopedSession
from .utils import db_transaction, get_or_create_submission, create_task, update_task_status, create_zenodo_record, create_asset

# Initialize database tables
def init_db():
    Base.metadata.create_all(bind=engine) 