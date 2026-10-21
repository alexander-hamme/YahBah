"""Config files and dirs resolve against the project root, not the process's working directory.

The API was once started from frontend/: it read no .env and no settings.yaml, and
PUT /settings failed with FileNotFoundError on config/settings.yaml.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_PROBE = """
import json
from yahbah import config
print(json.dumps({
    "root": str(config.PROJECT_ROOT),
    "settings_yaml": str(config._SETTINGS_PATH),
    "env_file": str(config.Settings.model_config["env_file"]),
    "prompts": config.settings.prompts_path,
    "personal": config.settings.personal_path,
    "artifacts": config.settings.artifacts_dir,
}))
"""


def test_paths_resolve_against_project_root_from_any_cwd(tmp_path):
    env = {**os.environ, "ARTIFACTS_DIR": "./artifacts", "PROMPTS_PATH": "config/prompts.yaml",
           "PERSONAL_PATH": "config/personal.yaml"}
    out = subprocess.run([sys.executable, "-c", _PROBE], cwd=tmp_path, env=env,
                         capture_output=True, text=True, check=True).stdout
    paths = json.loads(out.strip().splitlines()[-1])

    assert Path(paths["root"]) == ROOT
    assert Path(paths["settings_yaml"]) == ROOT / "config" / "settings.yaml"
    assert Path(paths["env_file"]) == ROOT / ".env"
    assert Path(paths["prompts"]) == ROOT / "config" / "prompts.yaml"
    assert Path(paths["personal"]) == ROOT / "config" / "personal.yaml"
    assert Path(paths["artifacts"]) == ROOT / "artifacts"


def test_absolute_overrides_are_kept(tmp_path):
    env = {**os.environ, "ARTIFACTS_DIR": str(tmp_path / "art")}
    out = subprocess.run([sys.executable, "-c", _PROBE], cwd=tmp_path, env=env,
                         capture_output=True, text=True, check=True).stdout
    assert json.loads(out.strip().splitlines()[-1])["artifacts"] == str(tmp_path / "art")


def test_defaults_resolve_against_project_root(monkeypatch):
    from yahbah import config
    for var in ("ARTIFACTS_DIR", "PROMPTS_PATH", "PERSONAL_PATH"):
        monkeypatch.delenv(var, raising=False)
    s = config.Settings(_env_file=None)
    assert Path(s.artifacts_dir) == ROOT / "artifacts"
    assert Path(s.prompts_path) == ROOT / "config" / "prompts.yaml"
    assert Path(s.personal_path) == ROOT / "config" / "personal.yaml"
