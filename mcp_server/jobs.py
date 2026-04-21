"""Async search job runner with TTL-based caching."""

from __future__ import annotations

import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from src.database.models import Listing, UserCriteria, AnalysisResult
from src.scraper.base import BaseScraper

logger = logging.getLogger(__name__)


@dataclass
class SearchJob:
    """A background scraping job."""
    job_id: str
    criteria: UserCriteria
    status: str = "running"  # running | completed | failed
    listings: list[Listing] = field(default_factory=list)
    platforms_done: list[str] = field(default_factory=list)
    platforms_failed: list[tuple[str, str]] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None

    @property
    def progress_message(self) -> str:
        total = len(self.platforms_done) + len(self.platforms_failed)
        return (
            f"{total}/4 platforms done, "
            f"{len(self.listings)} listings found"
        )

    @property
    def duration_seconds(self) -> float | None:
        if self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None


def _criteria_cache_key(c: UserCriteria) -> str:
    """Create a hashable key from criteria for cache lookup."""
    return json.dumps({
        "budget_min": c.budget_min,
        "budget_max": c.budget_max,
        "min_size_sqm": c.min_size_sqm,
        "min_rooms": c.min_rooms,
        "property_types": sorted(c.property_types),
        "districts": sorted(c.districts),
    }, sort_keys=True)


class JobStore:
    """Manages background scraping jobs with TTL cache."""

    def __init__(self, cache_ttl_minutes: int = 30):
        self._jobs: dict[str, SearchJob] = {}
        self._cache_keys: dict[str, str] = {}  # cache_key -> job_id
        self._executor = ThreadPoolExecutor(max_workers=4)
        self._cache_ttl = timedelta(minutes=cache_ttl_minutes)

    def get_job(self, job_id: str) -> SearchJob | None:
        return self._jobs.get(job_id)

    def find_cached(self, criteria: UserCriteria) -> SearchJob | None:
        """Find a completed job with matching criteria within TTL."""
        key = _criteria_cache_key(criteria)
        job_id = self._cache_keys.get(key)
        if not job_id:
            return None

        job = self._jobs.get(job_id)
        if not job or job.status != "completed":
            return None

        if job.completed_at and datetime.now() - job.completed_at > self._cache_ttl:
            # Expired
            del self._cache_keys[key]
            return None

        return job

    def start_job(self, criteria: UserCriteria, scrapers: list[BaseScraper]) -> SearchJob:
        """Start scraping in background thread, return job immediately."""
        job = SearchJob(
            job_id=str(uuid.uuid4())[:8],
            criteria=criteria,
        )
        self._jobs[job.job_id] = job

        # Register in cache index
        key = _criteria_cache_key(criteria)
        self._cache_keys[key] = job.job_id

        # Run scrapers in background
        self._executor.submit(self._run_scrapers, job, scrapers)
        return job

    def _run_scrapers(self, job: SearchJob, scrapers: list[BaseScraper]):
        """Runs in background thread. Updates job in-place."""
        try:
            for scraper in scrapers:
                try:
                    listings = scraper.search(job.criteria, max_pages=3)
                    job.listings.extend(listings)
                    job.platforms_done.append(scraper.PLATFORM_NAME)
                    logger.info(
                        f"[{job.job_id}] {scraper.PLATFORM_NAME}: "
                        f"{len(listings)} listings"
                    )
                except Exception as e:
                    error_msg = str(e)[:200]
                    job.platforms_failed.append((scraper.PLATFORM_NAME, error_msg))
                    logger.warning(
                        f"[{job.job_id}] {scraper.PLATFORM_NAME}: {error_msg}"
                    )
                finally:
                    try:
                        scraper.close()
                    except Exception:
                        pass

            job.status = "completed" if job.listings else (
                "failed" if not job.platforms_done else "completed"
            )
        except Exception as e:
            job.status = "failed"
            logger.error(f"[{job.job_id}] Job failed: {e}")
        finally:
            job.completed_at = datetime.now()

    def cleanup_old_jobs(self, max_age_hours: int = 6):
        """Remove old completed jobs to prevent memory growth."""
        cutoff = datetime.now() - timedelta(hours=max_age_hours)
        expired = [
            jid for jid, job in self._jobs.items()
            if job.completed_at and job.completed_at < cutoff
        ]
        for jid in expired:
            del self._jobs[jid]

    def shutdown(self):
        self._executor.shutdown(wait=False)
