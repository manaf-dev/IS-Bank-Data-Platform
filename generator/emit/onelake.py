"""Upload a local landing zone into a lakehouse's Files area over OneLake.

Uses the ADLS Gen2 SDK against the OneLake DFS endpoint with GUID addressing
(`<workspace id>/<item id>/Files/...`), authenticated as the signed-in Azure CLI user. No
secrets are read or stored. Uploads overwrite files of the same name, so re-running an
upload is idempotent.
"""

from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

ONELAKE_URL = "https://onelake.dfs.fabric.microsoft.com"


@dataclass(frozen=True)
class UploadResult:
    """What an upload did: files attempted, files that failed (with the error)."""

    uploaded: int
    failed: list[tuple[str, str]]


def remote_paths(local_root: Path, dest_prefix: str) -> list[tuple[Path, str]]:
    """Pair every local file under `local_root` with its destination path under `dest_prefix`."""
    pairs = []
    for path in sorted(p for p in local_root.rglob("*") if p.is_file()):
        relative = path.relative_to(local_root).as_posix()
        pairs.append((path, f"{dest_prefix.rstrip('/')}/{relative}"))
    return pairs


def upload_directory(
    local_root: Path,
    workspace_id: str,
    item_id: str,
    dest_prefix: str = "Files/landing",
    workers: int = 16,
) -> UploadResult:
    """Upload `local_root` recursively to `<workspace>/<item>/<dest_prefix>/`."""
    from azure.identity import AzureCliCredential
    from azure.storage.filedatalake import DataLakeServiceClient

    credential = AzureCliCredential()
    # Fetch a token once up front: otherwise every worker thread shells out to the Azure CLI
    # at the same moment and some of those calls fail before a retry succeeds.
    credential.get_token("https://storage.azure.com/.default")
    service = DataLakeServiceClient(ONELAKE_URL, credential=credential)
    fs = service.get_file_system_client(workspace_id)
    pairs = remote_paths(local_root, f"{item_id}/{dest_prefix}")

    def upload(pair: tuple[Path, str]) -> tuple[str, str] | None:
        local, remote = pair
        try:
            with local.open("rb") as data:
                fs.get_file_client(remote).upload_data(data, overwrite=True)
            return None
        except Exception as exc:  # noqa: BLE001 - report every failure, don't stop the batch
            return remote, f"{type(exc).__name__}: {exc}"[:300]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        failures = [f for f in pool.map(upload, pairs) if f is not None]
    return UploadResult(uploaded=len(pairs) - len(failures), failed=failures)


def list_remote(
    workspace_id: str, item_id: str, dest_prefix: str = "Files/landing"
) -> Iterable[str]:
    """Paths of every file under `<item>/<dest_prefix>` (for verifying an upload)."""
    from azure.identity import AzureCliCredential
    from azure.storage.filedatalake import DataLakeServiceClient

    service = DataLakeServiceClient(ONELAKE_URL, credential=AzureCliCredential())
    fs = service.get_file_system_client(workspace_id)
    for path in fs.get_paths(path=f"{item_id}/{dest_prefix}", recursive=True):
        if not path.is_directory:
            yield path.name
