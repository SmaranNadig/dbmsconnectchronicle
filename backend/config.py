"""Runtime configuration loaded from environment variables / .env."""
from __future__ import annotations

import os
from urllib.parse import quote
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = Path(os.environ.get("CC_RAW_DIR", DATA_DIR / "raw"))
MANIFEST_PATH = DATA_DIR / "metadata" / "dataset_manifest.json"
# overridable so tests can register a fixture dataset without touching the real catalog
CATALOG_PATH = Path(os.environ.get("CC_CATALOG", PROJECT_ROOT / "config" / "datasets.yaml"))
SCHEMA_MAPPING_DIR = Path(os.environ.get("CC_SCHEMA_MAPPING_DIR", PROJECT_ROOT / "config" / "schema_mappings"))
PIPELINE_VERSION = "0.1.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    postgres_user: str = "chronicle"
    postgres_password: str = ""
    postgres_db: str = "connectome"
    postgres_host: str = "localhost"
    coordinator_port: int = 55432
    shard1_port: int = 55433
    shard2_port: int = 55434
    shard3_port: int = 55435

    mongo_user: str = "chronicle"
    mongo_password: str = ""
    mongo_db: str = "connectome_meta"
    mongo_port: int = 57017
    cc_review_token: str = ''

    def pg_dsn(self, port: int | None = None, db: str | None = None) -> str:
        return (
            f"postgresql://{quote(self.postgres_user,safe='')}:{quote(self.postgres_password,safe='')}"
            f"@{self.postgres_host}:{port or self.coordinator_port}/{db or self.postgres_db}"
        )

    @property
    def shard_ports(self) -> list[int]:
        return [self.shard1_port, self.shard2_port, self.shard3_port]

    @property
    def mongo_uri(self) -> str:
        return (
            f"mongodb://{quote(self.mongo_user,safe='')}:{quote(self.mongo_password,safe='')}"
            f"@{self.postgres_host}:{self.mongo_port}/?authSource=admin"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def load_catalog() -> dict:
    with open(CATALOG_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)["datasets"]
