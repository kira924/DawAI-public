import argparse
import json
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url

from src.core.config import settings
from src.core.database import sessionLocal
from src.modules.catalog.titan_import import apply_titan_import, build_titan_import_plan

EXPECTED_REVISION = "5f2b8c4d7a91"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Profile or import the locally authorized Titan medicine catalog"
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path("Files"),
        help="Read-only Titan source directory",
    )
    parser.add_argument("--apply", action="store_true", help="Apply the import transaction")
    parser.add_argument(
        "--expected-host",
        help="Exact database hostname required for a write",
    )
    parser.add_argument(
        "--expected-database",
        help="Exact database name required for a write",
    )
    arguments = parser.parse_args()

    root = arguments.source_root.resolve()
    plan = build_titan_import_plan(
        drugeye_path=root / "DB" / "drugeye-for-titan.phy",
        branch_master_path=root / "DBI" / "tar.phy",
        new_drug_path=root / "DBI" / "New.drug.txt",
        interactions_path=root / "Pharm" / "DDI.Phy",
    )
    report: dict[str, object] = {
        "mode": "apply" if arguments.apply else "dry-run",
        "source_root": root.name,
        "file_checksums": plan.file_checksums,
        "planned": plan.stats,
    }
    if not arguments.apply:
        print(json.dumps(report, sort_keys=True))
        return 0

    if not arguments.expected_host or not arguments.expected_database:
        parser.error("--expected-host and --expected-database are required with --apply")
    database_url = make_url(str(settings.DATABASE_URL))
    if database_url.host != arguments.expected_host:
        parser.error("Configured database host does not match --expected-host")
    if database_url.database != arguments.expected_database:
        parser.error("Configured database name does not match --expected-database")

    with sessionLocal() as db:
        revision = db.scalar(text("SELECT version_num FROM alembic_version"))
        if revision != EXPECTED_REVISION:
            parser.error(
                f"Database must be at catalog revision {EXPECTED_REVISION}; found {revision}"
            )
        report["applied"] = apply_titan_import(db, plan)

    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
