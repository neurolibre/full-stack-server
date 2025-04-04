from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, Text, JSON, Enum
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
import enum
import datetime

Base = declarative_base()

class TaskStatus(enum.Enum):
    PENDING = "pending"
    RECEIVED = "received"
    STARTED = "started"
    SUCCESS = "success"
    FAILURE = "failure"
    REVOKED = "revoked"

class AssetType(enum.Enum):
    REPOSITORY = "repository"
    BOOK = "book"
    DATA = "data"
    DOCKER = "docker"
    MYST = "myst"

class Submission(Base):
    __tablename__ = 'submissions'
    
    id = Column(Integer, primary_key=True)
    issue_id = Column(Integer, nullable=False, unique=True, index=True)
    repository_url = Column(String(255), nullable=False)
    fork_url = Column(String(255))
    commit_hash = Column(String(40))
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    
    # Relationships
    tasks = relationship("Task", back_populates="submission")
    zenodo_records = relationship("ZenodoRecord", back_populates="submission")
    assets = relationship("Asset", back_populates="submission")
    
    def __repr__(self):
        return f"<Submission(issue_id={self.issue_id}, repository_url='{self.repository_url}')>"

class Task(Base):
    __tablename__ = 'tasks'
    
    id = Column(Integer, primary_key=True)
    submission_id = Column(Integer, ForeignKey('submissions.id'), nullable=False)
    celery_task_id = Column(String(255), nullable=False, index=True)
    task_name = Column(String(255), nullable=False)
    status = Column(Enum(TaskStatus), default=TaskStatus.PENDING)
    comment_id = Column(Integer)
    result = Column(JSON)
    error_message = Column(Text)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    
    # Relationships
    submission = relationship("Submission", back_populates="tasks")
    
    def __repr__(self):
        return f"<Task(task_name='{self.task_name}', status={self.status})>"

class ZenodoRecord(Base):
    __tablename__ = 'zenodo_records'
    
    id = Column(Integer, primary_key=True)
    submission_id = Column(Integer, ForeignKey('submissions.id'), nullable=False)
    record_type = Column(Enum(AssetType), nullable=False)
    deposit_id = Column(String(255), nullable=False)
    bucket_url = Column(String(255), nullable=False)
    doi = Column(String(255))
    is_published = Column(Boolean, default=False)
    metadata = Column(JSON)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    
    # Relationships
    submission = relationship("Submission", back_populates="zenodo_records")
    
    def __repr__(self):
        return f"<ZenodoRecord(record_type={self.record_type}, deposit_id='{self.deposit_id}')>"

class Asset(Base):
    __tablename__ = 'assets'
    
    id = Column(Integer, primary_key=True)
    submission_id = Column(Integer, ForeignKey('submissions.id'), nullable=False)
    asset_type = Column(Enum(AssetType), nullable=False)
    file_path = Column(String(255))
    zenodo_uploaded = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    
    # Relationships
    submission = relationship("Submission", back_populates="assets")
    
    def __repr__(self):
        return f"<Asset(asset_type={self.asset_type}, file_path='{self.file_path}')>" 