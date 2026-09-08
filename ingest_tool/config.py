"""Loads config.toml. This is the whole point of the "config-swappable provider" design:
switching from DeepSeek to OpenRouter/OpenAI/Anthropic/self-hosted is editing this file,
never touching Python.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

try:
    import tomllib  # stdlib, Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # backport, needed on 3.10 (e.g. the venv MinerU's Windows wheel requires)
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"


@dataclass
class VLMRole:
    model: str
    api_key: str | None = None  # inline in config.toml (gitignored) — simplest for local use
    api_key_env: str | None = None  # or read from an env var instead
    api_base: str | None = None

    def resolve_api_key(self) -> str | None:
        if self.api_key:
            return self.api_key
        return os.environ.get(self.api_key_env) if self.api_key_env else None


@dataclass
class CrawlConfig:
    max_depth: int = 2
    max_pages: int = 200
    respect_robots_txt: bool = True


@dataclass
class PapersConfig:
    default_sources: list[str] | None = None
    default_limit: int = 20

    def __post_init__(self) -> None:
        if self.default_sources is None:
            self.default_sources = ["arxiv", "semantic", "openalex"]


@dataclass
class Config:
    vlm: dict[str, VLMRole]
    crawl: CrawlConfig
    papers: PapersConfig


def load_config(path: Path | None = None) -> Config:
    path = path or DEFAULT_CONFIG_PATH
    if not path.exists():
        # No config file yet -> ship a working default (DeepSeek), matching config.example.toml.
        return Config(
            vlm={"default": VLMRole(model="deepseek/deepseek-v4-flash-vision-exp", api_key_env="DEEPSEEK_API_KEY")},
            crawl=CrawlConfig(),
            papers=PapersConfig(),
        )

    with path.open("rb") as f:
        raw = tomllib.load(f)

    vlm_raw = raw.get("vlm", {})
    vlm = {
        role: VLMRole(
            model=cfg["model"],
            api_key=cfg.get("api_key"),
            api_key_env=cfg.get("api_key_env"),
            api_base=cfg.get("api_base"),
        )
        for role, cfg in vlm_raw.items()
    }
    if not vlm:
        vlm = {"default": VLMRole(model="deepseek/deepseek-v4-flash-vision-exp", api_key_env="DEEPSEEK_API_KEY")}

    crawl_raw = raw.get("crawl", {})
    crawl = CrawlConfig(
        max_depth=crawl_raw.get("max_depth", 2),
        max_pages=crawl_raw.get("max_pages", 200),
        respect_robots_txt=crawl_raw.get("respect_robots_txt", True),
    )

    papers_raw = raw.get("papers", {})
    papers = PapersConfig(
        default_sources=papers_raw.get("default_sources"),
        default_limit=papers_raw.get("default_limit", 20),
    )

    return Config(vlm=vlm, crawl=crawl, papers=papers)
