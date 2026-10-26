"""Password persistence, masking and environment precedence checks, using temporary files."""
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "_Prj/SD_SMA_COMMON"))
sys.path.insert(0, str(ROOT / "_Launcher"))
sys.path.insert(0, str(ROOT / "_Prj/SD_SMA_DATA_COLLECTOR_QUERY_WEB"))
from sd_sma_common.password_store import prepare_password_mapping, migrate_password_mapping, resolve_password
from launcher_security import LauncherSecurityStore
from app.config_manager import UnifiedConfigStore

with TemporaryDirectory() as temp:
    directory = Path(temp)
    stored = prepare_password_mapping(directory, {"password": "sample"})
    assert stored == {"password": "sample"}
    assert prepare_password_mapping(directory, {"password": ""}, stored) == stored
    assert prepare_password_mapping(directory, {"clear_password": True}, stored)["password"] == ""
    assert migrate_password_mapping(directory, {"password_enc": "unreadable"}) == ({}, True)
    variable = "SD_SMA_PASSWORD_CHECK"
    os.environ[variable] = "override"
    try:
        assert resolve_password(directory, stored, variable) == "override"
        os.environ[variable] = ""
        assert resolve_password(directory, stored, variable) == ""
    finally:
        os.environ.pop(variable)
    assert resolve_password(directory, stored, variable) == "sample"
    store = LauncherSecurityStore(directory / "launcher.json")
    public = store.upsert_credential({"id": "test", "password": "sample"})
    assert "password" not in public and public["password_configured"]
    store.upsert_credential({"id": "test", "password": ""})
    assert store.credential_by_id("test")["password"] == "sample"
    assert json.loads(store.path.read_text())["credentials"][0]["password"] == "sample"
    query = UnifiedConfigStore(directory / "query")
    query.save_app_settings({"database": {"password": "db-sample"}})
    query.save_opcua_settings({"password": "opc-sample"})
    assert query.get_active_config()["opcua"]["password"] == "opc-sample"
    assert query.get_app_settings()["database"]["password"] == "db-sample"
    assert "password" not in query.get_public_app_settings()["database"]
    assert "password" not in query.get_public_opcua_settings()
    profile = query.get_public_profile()
    assert "password" not in profile["opcua"] and "password" not in profile["app_settings"]["database"]
    query.save_opcua_settings({"password": ""})
    assert query.get_active_config()["opcua"]["password"] == "opc-sample"
    query.save_opcua_settings({"clear_password": True})
    assert query.get_active_config()["opcua"]["password"] == ""
    assert not list(directory.rglob("*.key"))
print("password_store_check: OK")
