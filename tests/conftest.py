import os
import subprocess
import sys
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url

sys.dont_write_bytecode = True

os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg2://dawai_test:dawai_test@127.0.0.1:5432/dawai_test",
)
os.environ.setdefault("SECRET_KEY", "test-only-secret-that-must-never-be-used-in-production")


def _test_database_server_url() -> URL:
    configured_url = os.getenv("TEST_DATABASE_URL")
    if not configured_url:
        pytest.skip("TEST_DATABASE_URL is not configured")

    url = make_url(configured_url)
    if url.drivername not in {"postgresql", "postgresql+psycopg2"}:
        pytest.fail("TEST_DATABASE_URL must use PostgreSQL")
    if url.host not in {"127.0.0.1", "localhost", "::1"}:
        pytest.fail("TEST_DATABASE_URL must use a loopback host")
    if not url.database or not url.database.endswith("_test"):
        pytest.fail("TEST_DATABASE_URL database name must end with '_test'")

    return url


@pytest.fixture
def empty_postgresql_database_url() -> Iterator[str]:
    server_url = _test_database_server_url()
    database_name = f"dawai_{uuid.uuid4().hex}_test"
    admin_url = server_url.set(database="postgres")
    database_url = server_url.set(database=database_name)
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")

    with admin_engine.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')

    try:
        yield database_url.render_as_string(hide_password=False)
    finally:
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (database_name,),
            )
            connection.exec_driver_sql(f'DROP DATABASE "{database_name}"')
        admin_engine.dispose()


@pytest.fixture
def migrated_postgresql_database_url(empty_postgresql_database_url: str) -> str:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = empty_postgresql_database_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(result.stderr)
    return empty_postgresql_database_url
