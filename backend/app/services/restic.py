from typing import Optional, Tuple


async def restic_version() -> Optional[str]:
    return None


async def restic_cat_config(repo_path: str, password: str) -> Tuple[int, str, str]:
    return (-1, "", "not implemented")


async def restic_init(repo_path: str, password: str) -> Tuple[int, str, str]:
    return (-1, "", "not implemented")


async def restic_backup(
    repo_path: str,
    password: str,
    source_path: str,
    timeout_seconds: int,
    **kwargs,
) -> Tuple[int, str, str, Optional[dict]]:
    return (-1, "", "not implemented", None)


async def restic_snapshots(repo_path: str, password: str) -> Tuple[int, list, str]:
    return (-1, [], "not implemented")


async def restic_forget_prune(
    repo_path: str,
    password: str,
    timeout_seconds: int,
    **retention_flags,
) -> Tuple[int, str, str]:
    return (-1, "", "not implemented")


async def restic_prune(
    repo_path: str,
    password: str,
    timeout_seconds: int,
) -> Tuple[int, str, str]:
    return (-1, "", "not implemented")


async def restic_check(
    repo_path: str,
    password: str,
    mode: str,
    subset_percent: Optional[int],
    timeout_seconds: int,
) -> Tuple[int, str, str]:
    return (-1, "", "not implemented")


async def restic_unlock(repo_path: str, password: str) -> Tuple[int, str, str]:
    return (-1, "", "not implemented")
