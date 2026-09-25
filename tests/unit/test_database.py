"""Tests for database layer."""

import os
import tempfile
from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from src.storage.database import (
    Account,
    Database,
    Post,
    PublishAttempt,
    PublishJob,
    SchemaVersion,
    Video,
)
from src.storage.tokens import TokenEncryption, TokenEncryptionError, generate_key


@pytest.fixture
def temp_db_path():
    """Create a temporary database file path."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    # Cleanup - close any connections first
    try:
        if os.path.exists(path):
            os.unlink(path)
    except PermissionError:
        pass  # Windows may hold lock briefly


@pytest.fixture
def encryption_key():
    """Generate a test encryption key."""
    return generate_key()


@pytest.fixture
def encryption(encryption_key):
    """Create TokenEncryption instance."""
    return TokenEncryption(encryption_key.encode())


@pytest.fixture
def database(temp_db_path, encryption):
    """Create a test database instance."""
    db = Database(f"sqlite:///{temp_db_path}", encryption=encryption)
    db.init()
    yield db
    # Dispose engine to release file locks
    db.engine.dispose()


class TestDatabaseInitialization:
    """Tests for database initialization."""

    def test_init_creates_tables(self, database, temp_db_path):
        """Test that init creates all required tables."""
        assert os.path.exists(temp_db_path)

        with database.session():
            inspector = inspect(database.engine)
            tables = inspector.get_table_names()

            expected_tables = {
                "accounts",
                "videos",
                "posts",
                "publish_jobs",
                "publish_attempts",
                "schema_version",
            }
            assert expected_tables.issubset(set(tables))

    def test_init_creates_schema_version_table(self, database):
        """Test that schema_version table records migration."""
        with database.session() as session:
            versions = session.query(SchemaVersion).order_by(SchemaVersion.version).all()
            assert [v.version for v in versions] == [1, 2, 3]
            assert versions[0].description == "001_initial_schema"
            assert versions[1].description == "002_publishing"

    def test_init_is_idempotent(self, database, temp_db_path):
        """Test that running init twice doesn't destroy data."""
        # Add some test data
        with database.session() as session:
            account = Account(
                platform="instagram",
                platform_account_id="12345",
                username="test_user",
                access_token="token123",
                _encryption=database.encryption,
            )
            session.add(account)
            session.commit()
            account_id = account.id

        # Run init again
        database.init()

        # Data should still exist
        with database.session() as session:
            account = session.query(Account).filter_by(id=account_id).first()
            assert account is not None
            assert account.username == "test_user"

    def test_migrate_runs_pending_only(self, database):
        """Test that migrate only runs pending migrations."""
        # Migration 1 already applied
        applied_before = database.get_applied_migrations()
        assert 1 in applied_before

        # Run migrate again - should not error
        database.migrate()

        applied_after = database.get_applied_migrations()
        assert applied_before == applied_after

    def test_health_check(self, database):
        """Test health check returns True for valid database."""
        assert database.health_check() is True


class TestAccountModel:
    """Tests for Account model."""

    def test_create_account(self, database, encryption):
        """Test creating an account with encrypted tokens."""
        with database.session() as session:
            account = Account(
                platform="instagram",
                platform_account_id="ig_12345",
                username="test_user",
                display_name="Test User",
                access_token="access_token_abc",
                refresh_token="refresh_token_xyz",
                expires_at=datetime(2025, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
                status="active",
                _encryption=encryption,
            )
            session.add(account)
            session.commit()
            account_id = account.id

        with database.session() as session:
            account = session.query(Account).filter_by(id=account_id).first()
            assert account is not None
            assert account.platform == "instagram"
            assert account.platform_account_id == "ig_12345"
            assert account.username == "test_user"
            assert account.display_name == "Test User"
            assert account.status == "active"
            assert account.expires_at.replace(tzinfo=timezone.utc) == datetime(2025, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

            # Verify tokens are encrypted
            assert account.access_token_enc != "access_token_abc"
            assert account.refresh_token_enc != "refresh_token_xyz"

            # Verify decryption works
            assert account.get_access_token(encryption) == "access_token_abc"
            assert account.get_refresh_token(encryption) == "refresh_token_xyz"

    def test_create_account_without_refresh_token(self, database, encryption):
        """Test creating account without refresh token."""
        with database.session() as session:
            account = Account(
                platform="tiktok",
                platform_account_id="tt_67890",
                username="tiktok_user",
                access_token="access_only",
                _encryption=encryption,
            )
            session.add(account)
            session.commit()
            account_id = account.id

        with database.session() as session:
            account = session.query(Account).filter_by(id=account_id).first()
            assert account.get_refresh_token(encryption) is None

    def test_account_uniqueness_constraint(self, database, encryption):
        """Test that (platform, platform_account_id) must be unique."""
        with database.session() as session:
            account1 = Account(
                platform="instagram",
                platform_account_id="same_id",
                username="user1",
                access_token="token1",
                _encryption=encryption,
            )
            session.add(account1)
            session.commit()

        # New session to avoid flush issues
        with database.session() as session:
            account2 = Account(
                platform="instagram",
                platform_account_id="same_id",
                username="user2",
                access_token="token2",
                _encryption=encryption,
            )
            session.add(account2)
            with pytest.raises(IntegrityError):
                session.commit()

    def test_account_platform_check_constraint(self, database, encryption):
        """Test that platform must be one of allowed values."""
        with database.session() as session:
            account = Account(
                platform="invalid_platform",
                platform_account_id="123",
                username="user",
                access_token="token",
                _encryption=encryption,
            )
            session.add(account)
            with pytest.raises(IntegrityError):
                session.flush()

    def test_account_status_check_constraint(self, database, encryption):
        """Test that status must be one of allowed values."""
        with database.session() as session:
            account = Account(
                platform="instagram",
                platform_account_id="123",
                username="user",
                access_token="token",
                status="invalid_status",
                _encryption=encryption,
            )
            session.add(account)
            with pytest.raises(IntegrityError):
                session.flush()

    def test_account_default_status(self, database, encryption):
        """Test that default status is 'active'."""
        with database.session() as session:
            account = Account(
                platform="youtube",
                platform_account_id="yt_123",
                username="yt_user",
                access_token="token",
                _encryption=encryption,
            )
            session.add(account)
            session.commit()
            account_id = account.id

        with database.session() as session:
            account = session.query(Account).filter_by(id=account_id).first()
            assert account.status == "active"

    def test_account_encryption_roundtrip(self, database, encryption):
        """Test token encryption/decryption roundtrip via model methods."""
        with database.session() as session:
            account = Account(
                platform="instagram",
                platform_account_id="ig_123",
                username="user",
                access_token="original_access",
                refresh_token="original_refresh",
                _encryption=encryption,
            )
            session.add(account)
            session.commit()
            account_id = account.id

        with database.session() as session:
            account = session.query(Account).filter_by(id=account_id).first()

            # Test get_access_token
            assert account.get_access_token(encryption) == "original_access"

            # Test get_refresh_token
            assert account.get_refresh_token(encryption) == "original_refresh"

            # Test set_access_token
            account.set_access_token("new_access", encryption)
            session.commit()

            # Verify new token works
            assert account.get_access_token(encryption) == "new_access"

            # Test set_refresh_token
            account.set_refresh_token("new_refresh", encryption)
            session.commit()

            assert account.get_refresh_token(encryption) == "new_refresh"

            # Test set_refresh_token to None
            account.set_refresh_token(None, encryption)
            session.commit()

            assert account.get_refresh_token(encryption) is None


class TestVideoModel:
    """Tests for Video model."""

    def test_create_video(self, database):
        """Test creating a video record."""
        with database.session() as session:
            video = Video(
                filename="test_video.mp4",
                path="/videos/test_video.mp4",
                size_bytes=1024 * 1024 * 50,  # 50 MB
                duration_seconds=60,
                mime_type="video/mp4",
                width=1920,
                height=1080,
                checksum="abc123def456",
            )
            session.add(video)
            session.commit()
            video_id = video.id

        with database.session() as session:
            video = session.query(Video).filter_by(id=video_id).first()
            assert video is not None
            assert video.filename == "test_video.mp4"
            assert video.path == "/videos/test_video.mp4"
            assert video.size_bytes == 1024 * 1024 * 50
            assert video.duration_seconds == 60
            assert video.mime_type == "video/mp4"
            assert video.width == 1920
            assert video.height == 1080
            assert video.checksum == "abc123def456"

    def test_video_checksum_unique_constraint(self, database):
        """Test that video checksum must be unique."""
        with database.session() as session:
            video1 = Video(
                filename="video1.mp4",
                path="/videos/video1.mp4",
                size_bytes=1000,
                checksum="same_checksum",
            )
            session.add(video1)
            session.commit()

        with database.session() as session:
            video2 = Video(
                filename="video2.mp4",
                path="/videos/video2.mp4",
                size_bytes=2000,
                checksum="same_checksum",
            )
            session.add(video2)
            with pytest.raises(IntegrityError):
                session.commit()

    def test_video_cascade_delete_posts(self, database):
        """Test that deleting video cascades to posts."""
        with database.session() as session:
            video = Video(
                filename="test.mp4",
                path="/videos/test.mp4",
                size_bytes=1000,
            )
            session.add(video)
            session.commit()
            video_id = video.id

            post = Post(video_id=video_id, caption="Test caption")
            session.add(post)
            session.commit()
            post_id = post.id

        # Delete video
        with database.session() as session:
            video = session.query(Video).filter_by(id=video_id).first()
            session.delete(video)
            session.commit()

        # Post should be deleted too
        with database.session() as session:
            post = session.query(Post).filter_by(id=post_id).first()
            assert post is None


class TestPostModel:
    """Tests for Post model."""

    def test_create_post(self, database):
        """Test creating a post."""
        with database.session() as session:
            video = Video(
                filename="test.mp4",
                path="/videos/test.mp4",
                size_bytes=1000,
            )
            session.add(video)
            session.commit()

            post = Post(video_id=video.id, caption="Test caption #hashtag")
            session.add(post)
            session.commit()
            post_id = post.id

        with database.session() as session:
            post = session.query(Post).filter_by(id=post_id).first()
            assert post is not None
            assert post.video_id == video.id
            assert post.caption == "Test caption #hashtag"

    def test_post_foreign_key_to_video(self, database):
        """Test that post requires valid video_id."""
        with database.session() as session:
            post = Post(video_id=99999, caption="Test")  # Non-existent video
            session.add(post)
            with pytest.raises(IntegrityError):
                session.commit()


class TestPublishJobModel:
    """Tests for PublishJob model."""

    def test_create_publish_job(self, database, encryption):
        """Test creating a publish job."""
        with database.session() as session:
            # Create account
            account = Account(
                platform="instagram",
                platform_account_id="ig_123",
                username="user",
                access_token="token",
                _encryption=encryption,
            )
            session.add(account)
            session.commit()

            # Create video and post
            video = Video(filename="test.mp4", path="/videos/test.mp4", size_bytes=1000)
            session.add(video)
            session.commit()

            post = Post(video_id=video.id, caption="Test")
            session.add(post)
            session.commit()

            # Create job
            job = PublishJob(
                post_id=post.id,
                account_id=account.id,
                status="pending",
            )
            session.add(job)
            session.commit()
            job_id = job.id

        with database.session() as session:
            job = session.query(PublishJob).filter_by(id=job_id).first()
            assert job is not None
            assert job.post_id == post.id
            assert job.account_id == account.id
            assert job.status == "pending"
            assert job.retry_count == 0
            assert job.platform_media_id is None
            assert job.error_message is None
            assert job.published_at is None

    def test_publish_job_status_check_constraint(self, database, encryption):
        """Test that job status must be valid."""
        with database.session() as session:
            account = Account(
                platform="instagram",
                platform_account_id="ig_123",
                username="user",
                access_token="token",
                _encryption=encryption,
            )
            session.add(account)
            session.commit()

            video = Video(filename="test.mp4", path="/videos/test.mp4", size_bytes=1000)
            session.add(video)
            session.commit()

            post = Post(video_id=video.id, caption="Test")
            session.add(post)
            session.commit()

            job = PublishJob(
                post_id=post.id,
                account_id=account.id,
                status="invalid_status",
            )
            session.add(job)
            with pytest.raises(IntegrityError):
                session.flush()

    def test_publish_job_foreign_keys(self, database, encryption):
        """Test that job requires valid post_id and account_id."""
        with database.session() as session:
            job = PublishJob(post_id=99999, account_id=99999, status="pending")
            session.add(job)
            with pytest.raises(IntegrityError):
                session.commit()

    def test_publish_job_default_status(self, database, encryption):
        """Test that default job status is 'pending'."""
        with database.session() as session:
            account = Account(
                platform="instagram",
                platform_account_id="ig_123",
                username="user",
                access_token="token",
                _encryption=encryption,
            )
            session.add(account)
            session.commit()

            video = Video(filename="test.mp4", path="/videos/test.mp4", size_bytes=1000)
            session.add(video)
            session.commit()

            post = Post(video_id=video.id, caption="Test")
            session.add(post)
            session.commit()

            job = PublishJob(post_id=post.id, account_id=account.id)
            session.add(job)
            session.commit()
            job_id = job.id

        with database.session() as session:
            job = session.query(PublishJob).filter_by(id=job_id).first()
            assert job.status == "pending"


class TestPublishAttemptModel:
    """Tests for PublishAttempt model."""

    def test_create_publish_attempt(self, database, encryption):
        """Test creating a publish attempt."""
        with database.session() as session:
            # Create account, video, post, job
            account = Account(
                platform="instagram",
                platform_account_id="ig_123",
                username="user",
                access_token="token",
                _encryption=encryption,
            )
            session.add(account)
            session.commit()

            video = Video(filename="test.mp4", path="/videos/test.mp4", size_bytes=1000)
            session.add(video)
            session.commit()

            post = Post(video_id=video.id, caption="Test")
            session.add(post)
            session.commit()

            job = PublishJob(post_id=post.id, account_id=account.id, status="pending")
            session.add(job)
            session.commit()

            # Create attempt
            attempt = PublishAttempt(
                job_id=job.id,
                attempt_number=1,
                status="started",
                response_json='{"id": "media_123"}',
                error_json=None,
            )
            session.add(attempt)
            session.commit()
            attempt_id = attempt.id

        with database.session() as session:
            attempt = session.query(PublishAttempt).filter_by(id=attempt_id).first()
            assert attempt is not None
            assert attempt.job_id == job.id
            assert attempt.attempt_number == 1
            assert attempt.status == "started"
            assert attempt.response_json == '{"id": "media_123"}'
            assert attempt.error_json is None

    def test_publish_attempt_unique_job_attempt(self, database, encryption):
        """Test that (job_id, attempt_number) must be unique."""
        with database.session() as session:
            account = Account(
                platform="instagram",
                platform_account_id="ig_123",
                username="user",
                access_token="token",
                _encryption=encryption,
            )
            session.add(account)
            session.commit()

            video = Video(filename="test.mp4", path="/videos/test.mp4", size_bytes=1000)
            session.add(video)
            session.commit()

            post = Post(video_id=video.id, caption="Test")
            session.add(post)
            session.commit()

            job = PublishJob(post_id=post.id, account_id=account.id, status="pending")
            session.add(job)
            session.commit()

            attempt1 = PublishAttempt(job_id=job.id, attempt_number=1, status="started")
            session.add(attempt1)
            session.commit()

        with database.session() as session:
            attempt2 = PublishAttempt(job_id=job.id, attempt_number=1, status="started")
            session.add(attempt2)
            with pytest.raises(IntegrityError):
                session.commit()

    def test_publish_attempt_status_check_constraint(self, database, encryption):
        """Test that attempt status must be valid."""
        with database.session() as session:
            account = Account(
                platform="instagram",
                platform_account_id="ig_123",
                username="user",
                access_token="token",
                _encryption=encryption,
            )
            session.add(account)
            session.commit()

            video = Video(filename="test.mp4", path="/videos/test.mp4", size_bytes=1000)
            session.add(video)
            session.commit()

            post = Post(video_id=video.id, caption="Test")
            session.add(post)
            session.commit()

            job = PublishJob(post_id=post.id, account_id=account.id, status="pending")
            session.add(job)
            session.commit()

            attempt = PublishAttempt(
                job_id=job.id,
                attempt_number=1,
                status="invalid_status",
            )
            session.add(attempt)
            with pytest.raises(IntegrityError):
                session.flush()

    def test_publish_attempt_cascade_delete(self, database, encryption):
        """Test that deleting job cascades to attempts."""
        with database.session() as session:
            account = Account(
                platform="instagram",
                platform_account_id="ig_123",
                username="user",
                access_token="token",
                _encryption=encryption,
            )
            session.add(account)
            session.commit()

            video = Video(filename="test.mp4", path="/videos/test.mp4", size_bytes=1000)
            session.add(video)
            session.commit()

            post = Post(video_id=video.id, caption="Test")
            session.add(post)
            session.commit()

            job = PublishJob(post_id=post.id, account_id=account.id, status="pending")
            session.add(job)
            session.commit()
            job_id = job.id

            attempt = PublishAttempt(job_id=job_id, attempt_number=1, status="started")
            session.add(attempt)
            session.commit()
            attempt_id = attempt.id

        # Delete job
        with database.session() as session:
            job = session.query(PublishJob).filter_by(id=job_id).first()
            session.delete(job)
            session.commit()

        # Attempt should be deleted too
        with database.session() as session:
            attempt = session.query(PublishAttempt).filter_by(id=attempt_id).first()
            assert attempt is None


class TestRelationships:
    """Tests for model relationships."""

    def test_account_to_jobs_relationship(self, database, encryption):
        """Test account -> publish_jobs relationship."""
        with database.session() as session:
            account = Account(
                platform="instagram",
                platform_account_id="ig_123",
                username="user",
                access_token="token",
                _encryption=encryption,
            )
            session.add(account)
            session.commit()

            video = Video(filename="test.mp4", path="/videos/test.mp4", size_bytes=1000)
            session.add(video)
            session.commit()

            post = Post(video_id=video.id, caption="Test")
            post2 = Post(video_id=video.id, caption="Test 2")
            session.add_all([post, post2])
            session.commit()

            job1 = PublishJob(post_id=post.id, account_id=account.id, status="pending")
            job2 = PublishJob(post_id=post2.id, account_id=account.id, status="pending")
            session.add_all([job1, job2])
            session.commit()

        with database.session() as session:
            account = session.query(Account).filter_by(id=account.id).first()
            assert len(account.publish_jobs) == 2

    def test_post_to_jobs_relationship(self, database, encryption):
        """Test post -> publish_jobs relationship."""
        with database.session() as session:
            account = Account(
                platform="instagram",
                platform_account_id="ig_123",
                username="user",
                access_token="token",
                _encryption=encryption,
            )
            account2 = Account(
                platform="tiktok",
                platform_account_id="tt_123",
                username="user2",
                access_token="token",
                _encryption=encryption,
            )
            session.add_all([account, account2])
            session.commit()

            video = Video(filename="test.mp4", path="/videos/test.mp4", size_bytes=1000)
            session.add(video)
            session.commit()

            post = Post(video_id=video.id, caption="Test")
            session.add(post)
            session.commit()

            job1 = PublishJob(post_id=post.id, account_id=account.id, status="pending")
            job2 = PublishJob(post_id=post.id, account_id=account2.id, status="pending")
            session.add_all([job1, job2])
            session.commit()

        with database.session() as session:
            post = session.query(Post).filter_by(id=post.id).first()
            assert len(post.publish_jobs) == 2

    def test_duplicate_job_for_same_destination_rejected(self, database, encryption):
        """One job per (post, account): duplicate publishing jobs are rejected by the DB."""
        with database.session() as session:
            account = Account(
                platform="instagram", platform_account_id="ig_1", username="u",
                access_token="token", _encryption=encryption,
            )
            video = Video(filename="t.mp4", path="/videos/t.mp4", size_bytes=10)
            session.add_all([account, video])
            session.commit()
            post = Post(video_id=video.id, caption="c")
            session.add(post)
            session.commit()
            session.add(PublishJob(post_id=post.id, account_id=account.id))
            session.commit()
            session.add(PublishJob(post_id=post.id, account_id=account.id))
            with pytest.raises(IntegrityError):
                session.commit()

    def test_created_at_defaults_are_per_row(self, database):
        """Regression: created_at defaults were evaluated once at import time."""
        import time

        with database.session() as session:
            v1 = Video(filename="a.mp4", path="/a.mp4", size_bytes=1)
            session.add(v1)
            session.commit()
            time.sleep(0.01)
            v2 = Video(filename="b.mp4", path="/b.mp4", size_bytes=1)
            session.add(v2)
            session.commit()
            assert v2.created_at > v1.created_at

    def test_job_to_attempts_relationship(self, database, encryption):
        """Test job -> attempts relationship with ordering."""
        with database.session() as session:
            account = Account(
                platform="instagram",
                platform_account_id="ig_123",
                username="user",
                access_token="token",
                _encryption=encryption,
            )
            session.add(account)
            session.commit()

            video = Video(filename="test.mp4", path="/videos/test.mp4", size_bytes=1000)
            session.add(video)
            session.commit()

            post = Post(video_id=video.id, caption="Test")
            session.add(post)
            session.commit()

            job = PublishJob(post_id=post.id, account_id=account.id, status="pending")
            session.add(job)
            session.commit()

            attempt1 = PublishAttempt(job_id=job.id, attempt_number=1, status="started")
            attempt2 = PublishAttempt(job_id=job.id, attempt_number=2, status="failed")
            attempt3 = PublishAttempt(job_id=job.id, attempt_number=3, status="success")
            session.add_all([attempt3, attempt1, attempt2])  # Add out of order
            session.commit()

        with database.session() as session:
            job = session.query(PublishJob).filter_by(id=job.id).first()
            attempts = job.attempts
            assert len(attempts) == 3
            assert attempts[0].attempt_number == 1
            assert attempts[1].attempt_number == 2
            assert attempts[2].attempt_number == 3


class TestTokenEncryptionIntegration:
    """Integration tests for token encryption with database."""

    def test_token_encryption_persists_correctly(self, database, encryption, temp_db_path):
        """Test that encrypted tokens persist and decrypt correctly."""
        original_access = "access_token_abcdef123456"
        original_refresh = "refresh_token_ghijkl789012"

        with database.session() as session:
            account = Account(
                platform="instagram",
                platform_account_id="ig_123",
                username="user",
                access_token=original_access,
                refresh_token=original_refresh,
                _encryption=encryption,
            )
            session.add(account)
            session.commit()
            account_id = account.id

        # Create new database instance to simulate restart
        new_db = Database(str(database.engine.url), encryption=encryption)

        with new_db.session() as session:
            account = session.query(Account).filter_by(id=account_id).first()
            assert account.get_access_token(encryption) == original_access
            assert account.get_refresh_token(encryption) == original_refresh

    def test_different_encryption_key_fails_decrypt(self, database, encryption_key, temp_db_path):
        """Test that different encryption key fails to decrypt."""
        key1 = encryption_key
        key2 = generate_key()

        encryption1 = TokenEncryption(key1.encode())
        encryption2 = TokenEncryption(key2.encode())

        with database.session() as session:
            account = Account(
                platform="instagram",
                platform_account_id="ig_123",
                username="user",
                access_token="secret_token",
                _encryption=encryption1,
            )
            session.add(account)
            session.commit()
            account_id = account.id

        # Try to decrypt with wrong key
        new_db = Database(str(database.engine.url), encryption=encryption2)

        with new_db.session() as session:
            account = session.query(Account).filter_by(id=account_id).first()
            with pytest.raises(TokenEncryptionError):
                account.get_access_token(encryption2)


class TestIndexes:
    """Tests for database indexes."""

    def test_account_indexes_exist(self, database):
        """Test that expected indexes exist on accounts table."""
        inspector = inspect(database.engine)
        indexes = inspector.get_indexes("accounts")
        index_names = {idx["name"] for idx in indexes}

        assert "uq_accounts_platform_id" in index_names
        assert "idx_accounts_platform" in index_names
        assert "idx_accounts_status" in index_names

    def test_video_indexes_exist(self, database):
        """Test that expected indexes exist on videos table."""
        inspector = inspect(database.engine)
        indexes = inspector.get_indexes("videos")
        index_names = {idx["name"] for idx in indexes}

        assert "uq_videos_checksum" in index_names
        assert "idx_videos_path" in index_names

    def test_job_indexes_exist(self, database):
        """Test that expected indexes exist on publish_jobs table."""
        inspector = inspect(database.engine)
        indexes = inspector.get_indexes("publish_jobs")
        index_names = {idx["name"] for idx in indexes}

        assert "idx_jobs_post" in index_names
        assert "idx_jobs_account" in index_names
        assert "idx_jobs_status" in index_names
        assert "idx_jobs_next_retry" in index_names

    def test_attempt_indexes_exist(self, database):
        """Test that expected indexes exist on publish_attempts table."""
        inspector = inspect(database.engine)
        indexes = inspector.get_indexes("publish_attempts")
        index_names = {idx["name"] for idx in indexes}

        assert "idx_attempts_job" in index_names
        # Check unique constraints (SQLite doesn't list them as indexes)
        unique_constraints = inspector.get_unique_constraints("publish_attempts")
        unique_constraint_names = {uc["name"] for uc in unique_constraints}
        assert "uq_attempts_job_number" in unique_constraint_names

class TestMigrationUpgrade:
    """Regression: SQL chunks starting with comments were skipped; 002 must upgrade a v1 database."""

    def test_v1_database_upgrades_to_v2(self, temp_db_path, encryption):
        from pathlib import Path

        from sqlalchemy import text

        db = Database(f"sqlite:///{temp_db_path}", encryption=encryption)
        migrations = Path(__file__).resolve().parents[2] / "src" / "storage" / "migrations"
        # Build the schema from migration 001 alone (no create_all), like a pre-Phase-4 database.
        db.apply_migration(1, "001_initial_schema", (migrations / "001_initial_schema.sql").read_text())
        with db.session() as session:
            cols = {r[1] for r in session.execute(text("PRAGMA table_info(publish_jobs)"))}
        assert "status" in cols and "options_json" not in cols

        db.migrate()
        with db.session() as session:
            cols = {r[1] for r in session.execute(text("PRAGMA table_info(publish_jobs)"))}
            indexes = {r[1] for r in session.execute(text("PRAGMA index_list(publish_jobs)"))}
        assert "options_json" in cols
        assert "uq_jobs_post_account" in indexes
        assert db.get_applied_migrations() == [1, 2, 3]
        db.engine.dispose()
