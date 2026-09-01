from __future__ import annotations

import os
from pathlib import Path

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite:///./test_vision_vitals.db"
os.environ["STORAGE_PATH"] = "./test_storage"
os.environ["AI_PROVIDER"] = "mock"
os.environ["TRUSTED_HOSTS"] = "testserver,localhost"
os.environ["JWT_SECRET"] = "test-access-secret-that-is-at-least-32-characters"
os.environ["JWT_REFRESH_SECRET"] = "test-refresh-secret-that-is-at-least-32-characters"

import pytest
from fastapi.testclient import TestClient

from vision_vitals.db import Base, engine
from vision_vitals.main import app


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    storage = Path("./test_storage")
    storage.mkdir(exist_ok=True)
    for file in storage.iterdir():
        if file.is_file():
            file.unlink()
    yield


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def register(client: TestClient, email: str, password: str = "correct horse battery staple") -> dict:
    response = client.post("/api/v1/auth/register", json={"email": email, "password": password})
    assert response.status_code == 201, response.text
    return response.json()["data"]


def auth_headers(data: dict) -> dict:
    return {"Authorization": f"Bearer {data['tokens']['access_token']}"}