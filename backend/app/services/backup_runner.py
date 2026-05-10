import asyncio
import uuid
from typing import Dict, Optional, Set

_active_jobs: Set[uuid.UUID] = set()
_job_locks: Dict[uuid.UUID, asyncio.Lock] = {}


async def run_backup(job_id: uuid.UUID, run_id: Optional[uuid.UUID] = None) -> None:
    pass
