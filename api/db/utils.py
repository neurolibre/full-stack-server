from contextlib import contextmanager
from .session import ScopedSession
from .models import Base, Submission, Task, ZenodoRecord, Asset, TaskStatus, AssetType
from sqlalchemy.exc import SQLAlchemyError
import logging

logger = logging.getLogger(__name__)

@contextmanager
def db_transaction():
    """
    Context manager for database transactions with automatic commit/rollback.
    """
    session = ScopedSession()
    try:
        yield session
        session.commit()
    except SQLAlchemyError as e:
        session.rollback()
        logger.error(f"Database error: {str(e)}")
        raise
    finally:
        session.close()

def get_or_create_submission(db, issue_id, repository_url):
    """
    Get an existing submission or create a new one if it doesn't exist.
    """
    submission = db.query(Submission).filter(Submission.issue_id == issue_id).first()
    if not submission:
        submission = Submission(
            issue_id=issue_id,
            repository_url=repository_url
        )
        db.add(submission)
        db.flush()  # Flush to get the ID without committing
    return submission

def create_task(db, submission_id, celery_task_id, task_name, comment_id=None):
    """
    Create a new task record.
    """
    task = Task(
        submission_id=submission_id,
        celery_task_id=celery_task_id,
        task_name=task_name,
        status=TaskStatus.RECEIVED,
        comment_id=comment_id
    )
    db.add(task)
    db.flush()
    return task

def update_task_status(db, celery_task_id, status, result=None, error_message=None):
    """
    Update the status of a task.
    """
    task = db.query(Task).filter(Task.celery_task_id == celery_task_id).first()
    if task:
        task.status = status
        if result is not None:
            task.result = result
        if error_message is not None:
            task.error_message = error_message
        db.flush()
    return task

def create_zenodo_record(db, submission_id, record_type, deposit_id, bucket_url, metadata=None):
    """
    Create a new Zenodo record.
    """
    zenodo_record = ZenodoRecord(
        submission_id=submission_id,
        record_type=record_type,
        deposit_id=deposit_id,
        bucket_url=bucket_url,
        metadata=metadata
    )
    db.add(zenodo_record)
    db.flush()
    return zenodo_record

def create_asset(db, submission_id, asset_type, file_path=None):
    """
    Create a new asset record.
    """
    asset = Asset(
        submission_id=submission_id,
        asset_type=asset_type,
        file_path=file_path
    )
    db.add(asset)
    db.flush()
    return asset 