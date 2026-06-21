from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_root_compose_entrypoint_pins_root_as_the_environment_project_directory():
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

    assert "path: deploy/docker-compose.yaml" in compose
    assert "project_directory: deploy" in compose
    assert "env_file: .env" in compose
    assert "COMPOSE := docker compose -f compose.yaml" in makefile
