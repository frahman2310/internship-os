"""Single settings object. Loaded from environment / .env, validated once at startup.

Deliberately fails loudly on missing external tools rather than at the moment of use —
a broken pdflatex path should surface in `doctor`, not halfway through a CV compile.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent



class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- storage -----------------------------------------------------------
    db_url: str = Field(default=f"sqlite+aiosqlite:///{REPO_ROOT}/var/internship.db")
    artifacts_dir: Path = Field(default=REPO_ROOT / "artifacts")
    var_dir: Path = Field(default=REPO_ROOT / "var")

    # --- LLM (OmniRoute gateway, OpenAI-compatible) ------------------------
    # Falls back to the gateway credentials already in the environment, so no secret is
    # ever copied into this repo or into a config file. Set LLM_API_KEY to override.
    # Pinned to the local OmniRoute gateway on purpose: ANTHROPIC_BASE_URL in a shell may
    # legitimately point at the real Anthropic API, and research is meant to run on the
    # gateway's free models. Override with LLM_BASE_URL.
    llm_base_url: str = Field(default="http://localhost:20128/v1")
    llm_api_key: str = Field(
        default_factory=lambda: os.environ.get("ANTHROPIC_AUTH_TOKEN") or "not-needed"
    )
    # Research is outsourced to free models; first that answers wins. The tail is the
    # cheap/paid tier: free providers go down or run dry regularly, and a research run
    # that stops entirely is worse than one that costs a little.
    llm_research_models: str = Field(
        default=(
            "gemini/gemini-3.6-flash,"
            "llm7/DeepSeek-V4-Flash-0731,auto/best-free,oc/big-pickle,auto/best-chat"
        )
    )
    # Strategy + drafting want quality. The chain ends in free models on purpose: if a
    # paid provider is out of quota (429/402), the client falls through rather than
    # stopping work. Degraded output beats no output when a deadline is days away.
    llm_reasoning_models: str = Field(
        default="auto/best-reasoning,auto/best-chat,auto/best-free,oc/deepseek-v4-flash-free"
    )
    llm_timeout_s: float = Field(default=120.0)
    # Free provider pools drain and refill; one backoff pass beats failing a run.
    llm_max_passes: int = Field(default=2)
    llm_retry_delay_s: float = Field(default=8.0)
    llm_offline: bool = Field(default=False)
    research_budget_usd: float = Field(default=1.0)

    # --- external tools ----------------------------------------------------
    pdflatex_path: Path = Field(
        default=Path.home() / "Library/TinyTeX/bin/universal-darwin/pdflatex"
    )
    trackr_cli_path: Path = Field(
        default=Path(
            "/Users/user/Jobs: CV/ai-job-search/.agents/skills/trackr-search/cli/src/cli.ts"
        )
    )
    bun_path: str = Field(default="bun")

    # --- source profile (canonical candidate data lives in the fork for now) --
    profile_md_path: Path = Field(
        default=Path(
            "/Users/user/Jobs: CV/ai-job-search/.claude/skills/"
            "job-application-assistant/01-candidate-profile.md"
        )
    )
    master_cv_path: Path = Field(
        default=Path("/Users/user/Jobs: CV/ai-job-search/cv/main_user_master.tex")
    )

    def research_model_chain(self) -> list[str]:
        return [m.strip() for m in self.llm_research_models.split(",") if m.strip()]

    def reasoning_model_chain(self) -> list[str]:
        return [m.strip() for m in self.llm_reasoning_models.split(",") if m.strip()]

    def ensure_dirs(self) -> None:
        self.var_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
