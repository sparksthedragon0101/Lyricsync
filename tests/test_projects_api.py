import io

import pytest
from fastapi.testclient import TestClient

from lyricsync_web.app import main
from lyricsync_web.app.projects import Projects


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    """Provide a TestClient with an isolated projects directory."""
    projects = Projects(tmp_path)
    monkeypatch.setattr(main, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(main, "projects", projects)

    async def _stub_start_worker():
        return None

    monkeypatch.setattr(main, "start_worker", _stub_start_worker)

    with TestClient(main.app) as client:
        yield client, projects


def _sample_files():
    return {"audio": ("track.mp3", io.BytesIO(b"123"), "audio/mpeg")}


def test_html_form_posts_redirect(api_client):
    client, projects = api_client

    response = client.post(
        "/api/projects",
        data={"name": "Demo Track"},
        files=_sample_files(),
        headers={"accept": "text/html"},
        allow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/projects/demo-track"
    assert projects.get("demo-track")


def test_fetch_form_posts_receive_json(api_client):
    client, projects = api_client

    response = client.post(
        "/api/projects",
        data={"name": "JSON Track"},
        files=_sample_files(),
        headers={"accept": "application/json"},
        allow_redirects=False,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["slug"] == "json-track"
    assert payload["paths"]["audio"]
    assert projects.get("json-track")


def test_legacy_create_wrapper_returns_json(api_client):
    client, projects = api_client

    response = client.post(
        "/api/projects/create",
        data={"name": "Legacy Cut"},
        files=_sample_files(),
        headers={"accept": "application/json"},
        allow_redirects=False,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["slug"] == "legacy-cut"
    assert projects.get("legacy-cut")
