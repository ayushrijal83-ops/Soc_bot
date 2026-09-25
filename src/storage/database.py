"""SQLite database layer with SQLAlchemy ORM and migrations."""

import os
import sys
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Session,
    relationship,
    sessionmaker,
)

from src.storage.tokens import TokenEncryption, TokenEncryptionError


class Base(DeclarativeBase):
    """Base class for all models."""


class Account(Base):
    """Connected social media account."""

    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(20), nullable=False)
    platform_account_id = Column(String(100), nullable=False)
    username = Column(String(100), nullable=False)
    display_name = Column(String(200), nullable=True)
    access_token_enc = Column("access_token_enc", String(500), nullable=False)
    refresh_token_enc = Column("refresh_token_enc", String(500), nullable=True)
    expires_at = Column(DateTime, nullable=True)
    status = Column(String(20), nullable=False, default="active")
    meta_json = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("platform", "platform_account_id", name="uq_accounts_platform_id"),
        Index("idx_accounts_platform", "platform"),
        Index("idx_accounts_status", "status"),
        CheckConstraint("platform IN ('instagram','tiktok','youtube')", name="ck_accounts_platform"),
        CheckConstraint("status IN ('active','expired','revoked','disconnected')", name="ck_accounts_status"),
    )

    publish_jobs = relationship("PublishJob", back_populates="account", cascade="all, delete-orphan")

    VALID_PLATFORMS = ("instagram", "tiktok", "youtube")
    VALID_STATUSES = ("active", "expired", "revoked", "disconnected")

    def __init__(self, **kwargs):
        encryption = kwargs.pop("_encryption", None)
        access_token = kwargs.pop("access_token", None)
        refresh_token = kwargs.pop("refresh_token", None)
        super().__init__(**kwargs)
        if encryption and access_token:
            self.access_token_enc = encryption.encrypt(access_token).decode()
        if encryption and refresh_token:
            self.refresh_token_enc = encryption.encrypt(refresh_token).decode()

    def set_access_token(self, token: str, encryption: TokenEncryption) -> None:
        """Set and encrypt access token."""
        self.access_token_enc = encryption.encrypt(token).decode()
        self.updated_at = datetime.now(timezone.utc)

    def set_refresh_token(self, token: str | None, encryption: TokenEncryption) -> None:
        """Set and encrypt refresh token."""
        if token:
            self.refresh_token_enc = encryption.encrypt(token).decode()
        else:
            self.refresh_token_enc = None
        self.updated_at = datetime.now(timezone.utc)

    def get_access_token(self, encryption: TokenEncryption) -> str:
        """Get decrypted access token."""
        return encryption.decrypt(self.access_token_enc.encode())

    def get_refresh_token(self, encryption: TokenEncryption) -> str | None:
        """Get decrypted refresh token."""
        if self.refresh_token_enc:
            return encryption.decrypt(self.refresh_token_enc.encode())
        return None


class Video(Base):
    """Video file metadata."""

    __tablename__ = "videos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String(255), nullable=False)
    path = Column(String(500), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    duration_seconds = Column(Integer, nullable=True)
    mime_type = Column(String(100), nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    checksum = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("checksum", name="uq_videos_checksum"),
        Index("idx_videos_path", "path"),
    )

    posts = relationship("Post", back_populates="video", cascade="all, delete-orphan")


class Post(Base):
    """User-created post (video + caption)."""

    __tablename__ = "posts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    video_id = Column(Integer, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False)
    caption = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_posts_video", "video_id"),
    )

    video = relationship("Video", back_populates="posts")
    publish_jobs = relationship("PublishJob", back_populates="post", cascade="all, delete-orphan")


class PublishJob(Base):
    """Individual publishing job per destination (platform + account)."""

    __tablename__ = "publish_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    post_id = Column(Integer, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    platform_media_id = Column(String(100), nullable=True)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    next_retry_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now(timezone.utc))
    published_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_jobs_post", "post_id"),
        Index("idx_jobs_account", "account_id"),
        Index("idx_jobs_status", "status"),
        Index("idx_jobs_next_retry", "next_retry_at"),
        CheckConstraint("status IN ('pending','uploading','processing','published','failed','retrying')", name="ck_jobs_status"),
    )

    post = relationship("Post", back_populates="publish_jobs")
    account = relationship("Account", back_populates="publish_jobs")
    attempts = relationship("PublishAttempt", back_populates="job", cascade="all, delete-orphan", order_by="PublishAttempt.attempt_number")

    VALID_STATUSES = ("pending", "uploading", "processing", "published", "failed", "retrying")


class PublishAttempt(Base):
    """Detailed attempt history per job."""

    __tablename__ = "publish_attempts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("publish_jobs.id", ondelete="CASCADE"), nullable=False)
    attempt_number = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False)
    response_json = Column(Text, nullable=True)
    error_json = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=False, default=datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("idx_attempts_job", "job_id"),
        UniqueConstraint("job_id", "attempt_number", name="uq_attempts_job_number"),
        CheckConstraint("status IN ('started','uploading','processing','success','failed')", name="ck_attempts_status"),
    )

    job = relationship("PublishJob", back_populates="attempts")

    VALID_STATUSES = ("started", "uploading", "processing", "success", "failed")


class SchemaVersion(Base):
    """Tracks applied migration versions."""

    __tablename__ = "schema_version"

    version = Column(Integer, primary_key=True)
    applied_at = Column(DateTime, nullable=False, default=datetime.now(timezone.utc))
    description = Column(String(200), nullable=True)


class Database:
    """Database manager handling connection, migrations, and sessions."""

    def __init__(self, database_url: str | None = None, encryption: TokenEncryption | None = None):
        """
        Initialize database connection.

        Args:
            database_url: SQLAlchemy database URL. Defaults to DATABASE_URL env var or sqlite:///data/publisher.db
            encryption: TokenEncryption instance for token encryption/decryption.
        """
        if database_url is None:
            database_url = os.environ.get("DATABASE_URL", "sqlite:///data/publisher.db")

        # Convert URL object to string if needed
        database_url = str(database_url)

        # Ensure data directory exists for SQLite
        if database_url.startswith("sqlite:///"):
            db_path = database_url.replace("sqlite:///", "")
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self.encryption = encryption

        # Create engine with SQLite pragmas configured via event listener
        self.engine = create_engine(database_url, echo=False, future=True)
        
        # Enable foreign keys and CHECK constraints for SQLite - register BEFORE any connections
        if database_url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def set_sqlite_pragma(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA ignore_check_constraints=OFF")
                cursor.close()

        self.Session = sessionmaker(bind=self.engine, class_=Session, expire_on_commit=False)

    def create_all(self) -> None:
        """Create all tables."""
        Base.metadata.create_all(self.engine)

    def drop_all(self) -> None:
        """Drop all tables (use with caution)."""
        Base.metadata.drop_all(self.engine)

    def get_applied_migrations(self) -> list[int]:
        """Get list of applied migration versions."""
        with self.session() as session:
            try:
                versions = session.query(SchemaVersion.version).order_by(SchemaVersion.version).all()
                return [v[0] for v in versions]
            except Exception:  # noqa: BLE001
                # Table might not exist yet
                return []

    def apply_migration(self, version: int, description: str, sql: str) -> None:
        """Apply a single migration."""
        with self.session() as session:
            # Execute migration SQL - filter out comments and empty lines
            for statement in sql.split(";"):
                statement = statement.strip()
                if statement and not statement.startswith("--"):
                    session.execute(text(statement))
            # Record migration
            session.add(SchemaVersion(version=version, description=description))
            session.commit()

    def migrate(self, migrations_dir: str | None = None) -> None:
        """
        Run all pending migrations.

        Args:
            migrations_dir: Directory containing migration SQL files.
                          Defaults to src/storage/migrations/
        """
        if migrations_dir is None:
            migrations_dir = Path(__file__).parent / "migrations"

        applied = set(self.get_applied_migrations())

        for migration_file in sorted(Path(migrations_dir).glob("*.sql")):
            # Extract version from filename (e.g., 001_initial_schema.sql -> 1)
            try:
                version = int(migration_file.stem.split("_")[0])
            except (ValueError, IndexError):
                print(f"Skipping invalid migration filename: {migration_file.name}")
                continue

            if version in applied:
                print(f"Migration {version} already applied, skipping")
                continue

            print(f"Applying migration {version}: {migration_file.name}")
            sql = migration_file.read_text()
            self.apply_migration(version, migration_file.stem, sql)
            print(f"Migration {version} applied successfully")

    def init(self) -> None:
        """Initialize database: create tables and run migrations."""
        print("Initializing database...")
        self.create_all()
        print("Tables created.")
        self.migrate()
        print("Migrations applied.")
        print("Database initialization complete.")

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        """Get a database session."""
        session = self.Session()
        try:
            yield session
            # Only commit if transaction is still active and not already rolled back
            if session.in_transaction() and session.get_transaction().is_active:
                session.commit()
        except Exception:
            # Rollback if transaction is still active
            if session.in_transaction() and session.get_transaction().is_active:
                session.rollback()
            raise
        finally:
            session.close()

    def health_check(self) -> bool:
        """Check if database is accessible."""
        try:
            with self.session() as session:
                session.execute(text("SELECT 1"))
            return True
        except Exception:  # noqa: BLE001
            return False


def get_database() -> Database:
    """Get a Database instance with encryption from environment."""
    encryption = None
    try:
        encryption = TokenEncryption()
    except TokenEncryptionError:
        pass  # Encryption optional for some operations
    return Database(encryption=encryption)


# CLI entry point
def main():
    """CLI entry point for database commands."""
    if len(sys.argv) < 2:
        print("Usage: python -m src.storage.database <command>")
        print("Commands: init, migrate, health")
        sys.exit(1)

    command = sys.argv[1]
    db = get_database()

    if command == "init":
        db.init()
    elif command == "migrate":
        db.migrate()
    elif command == "health":
        if db.health_check():
            print("Database connection OK")
            sys.exit(0)
        else:
            print("Database connection FAILED")
            sys.exit(1)
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()