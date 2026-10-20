from pathlib import Path

from generator.emit.onelake import remote_paths


def test_remote_paths_mirror_the_local_tree(tmp_path: Path):
    (tmp_path / "crm" / "2024" / "01").mkdir(parents=True)
    (tmp_path / "crm" / "2024" / "01" / "a.csv").write_text("x")
    (tmp_path / "core.parquet").write_text("y")
    pairs = remote_paths(tmp_path, "item-id/Files/landing/")
    assert [remote for _, remote in pairs] == [
        "item-id/Files/landing/core.parquet",
        "item-id/Files/landing/crm/2024/01/a.csv",
    ]
