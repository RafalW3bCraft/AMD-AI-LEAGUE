"""Configuration loader — env-overridable settings for the agent."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class AgentConfig:
    # Search
    search_provider: str = "duckduckgo"   # "duckduckgo" | "stub"
    search_topk: int = 6                  # hits per variant
    # Fetch budget
    max_pages: int = 8                    # hard cap per query
    max_hops: int = 2
    fetch_timeout_s: float = 8.0
    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    respect_robots: bool = True
    # Extraction
    max_text_chars: int = 200_000         # per-page cap after HTML strip
    max_html_bytes: int = 5_000_000       # raw HTML cap BEFORE strip
    # Verification
    min_support: int = 1                  # min source count to accept
    # Optional stub corpus for offline tests
    stub_corpus: list = field(default_factory=list)

    # Paths used by the CLI entrypoint
    output_dir: str = os.environ.get("MATCH4_OUTPUT_DIR", "/app/output")

    # Deadline / budget.
    query_deadline_s: float = 28.0        # total per-query wall clock
    output_reserve_s: float = 2.0         # reserved for output writing

    # Security limits.
    allow_local_network: bool = False     # block private/loopback/etc by default
    max_redirects: int = 5                # hop cap per fetch
    max_concurrent_fetches: int = 4       # global semaphore for fetches


def load_config() -> AgentConfig:
    """Build a config from environment variables."""
    def _bool(name: str, default: str = "0") -> bool:
        return os.environ.get(name, default).lower() not in ("0", "false", "no", "")
    return AgentConfig(
        search_provider=os.environ.get("MATCH4_SEARCH_PROVIDER", "duckduckgo"),
        search_topk=int(os.environ.get("MATCH4_SEARCH_TOPK", "6")),
        max_pages=int(os.environ.get("MATCH4_MAX_PAGES", "8")),
        max_hops=int(os.environ.get("MATCH4_MAX_HOPS", "2")),
        fetch_timeout_s=float(os.environ.get("MATCH4_FETCH_TIMEOUT_S", "8")),
        respect_robots=_bool("MATCH4_RESPECT_ROBOTS", "1"),
        max_text_chars=int(os.environ.get("MATCH4_MAX_TEXT_CHARS", "200000")),
        max_html_bytes=int(os.environ.get("MATCH4_MAX_HTML_BYTES", "5000000")),
        min_support=int(os.environ.get("MATCH4_MIN_SUPPORT", "1")),
        output_dir=os.environ.get("MATCH4_OUTPUT_DIR", "/app/output"),
        query_deadline_s=float(os.environ.get("MATCH4_QUERY_DEADLINE_S", "28")),
        output_reserve_s=float(os.environ.get("MATCH4_OUTPUT_RESERVE_S", "2")),
        allow_local_network=_bool("MATCH4_ALLOW_LOCAL_NETWORK", "0"),
        max_redirects=int(os.environ.get("MATCH4_MAX_REDIRECTS", "5")),
        max_concurrent_fetches=int(os.environ.get("MATCH4_MAX_CONCURRENT_FETCHES", "4")),
    )