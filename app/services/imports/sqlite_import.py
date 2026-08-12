from __future__ import annotations

import sqlite3
import tempfile
from collections import Counter
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ImportBatchStatus, KeyStatus, KeyType, Plan, VPNKey
from app.repositories.audit_logs import AuditLogsRepository
from app.repositories.import_batches import ImportBatchesRepository
from app.services.security import KeyProtector
from app.services.transactions import transactional


class SqliteImportService:
    format_version = "golden-vpn.typed-keys.v1"
    allowed_types = {KeyType.AWG.value, KeyType.TROJAN.value, KeyType.HYSTERIA.value}
    allowed_statuses = {KeyStatus.AVAILABLE.value, KeyStatus.ISSUED.value}
    required_columns = {
        "key_type",
        "label",
        "key_status",
        "config_text",
        "plan_code",
        "external_ref",
        "comment",
        "expires_at",
    }

    def __init__(
        self,
        *,
        session: AsyncSession,
        import_batches_repo: ImportBatchesRepository,
        audit_logs_repo: AuditLogsRepository,
        key_protector: KeyProtector,
    ) -> None:
        self._session = session
        self._import_batches_repo = import_batches_repo
        self._audit_logs_repo = audit_logs_repo
        self._key_protector = key_protector

    async def preview(self, *, filename: str, content: bytes) -> dict:
        parsed = await self._parse_and_validate(content)
        return {
            "filename": filename,
            "rows_total": parsed["rows_total"],
            "rows_valid": len(parsed["valid_rows"]),
            "rows_rejected": len(parsed["errors"]),
            "errors": parsed["errors"],
            "types": dict(Counter(row["key_type"] for row in parsed["valid_rows"])),
            "statuses": dict(Counter(row["key_status"] for row in parsed["valid_rows"])),
        }

    async def import_file(self, *, filename: str, content: bytes, uploaded_by_user_id: int | None) -> dict:
        parsed = await self._parse_and_validate(content)
        async with transactional(self._session):
            batch = await self._import_batches_repo.create(
                uploaded_by_user_id=uploaded_by_user_id,
                source_filename=filename,
                rows_total=parsed["rows_total"],
                rows_imported=len(parsed["valid_rows"]),
                rows_rejected=len(parsed["errors"]),
                status=ImportBatchStatus.COMPLETED.value,
                report_json={"errors": parsed["errors"], "format": self.format_version},
            )
            for row in parsed["valid_rows"]:
                self._session.add(
                    VPNKey(
                        plan_id=row["plan_id"],
                        key_value_encrypted=self._key_protector.encrypt(row["config_text"]),
                        key_fingerprint=row["fingerprint"],
                        key_type=row["key_type"],
                        external_ref=row["external_ref"],
                        comment=row["comment"],
                        expires_at=row["expires_at"],
                        status=row["key_status"],
                        imported_batch_id=batch.id,
                    )
                )
            await self._audit_logs_repo.add(
                actor_user_id=uploaded_by_user_id,
                entity_type="import_batch",
                entity_id=str(batch.id),
                action="sqlite_typed_keys_import_completed",
                payload_json={
                    "batch_id": batch.id,
                    "filename": filename,
                    "rows_total": parsed["rows_total"],
                    "rows_imported": len(parsed["valid_rows"]),
                    "rows_rejected": len(parsed["errors"]),
                },
                correlation_id=f"import-batch:{batch.id}",
            )
        return {
            "batch_id": batch.id,
            "rows_total": parsed["rows_total"],
            "rows_imported": len(parsed["valid_rows"]),
            "rows_rejected": len(parsed["errors"]),
            "errors": parsed["errors"],
        }

    async def _parse_and_validate(self, content: bytes) -> dict:
        if not content.startswith(b"SQLite format 3\x00"):
            raise ValueError("File is not SQLite")
        if len(content) > 50 * 1024 * 1024:
            raise ValueError("SQLite import is limited to 50 MB")

        raw_rows = self._read_rows(content)
        plan_codes = {row["plan_code"] for row in raw_rows if row["plan_code"]}
        plans = {
            plan.code: plan
            for plan in await self._session.scalars(select(Plan).where(Plan.code.in_(plan_codes)))
        }
        fingerprints = [self._key_protector.fingerprint(row["config_text"]) for row in raw_rows if row["config_text"]]
        fingerprint_counter = Counter(fingerprints)
        existing_fingerprints = set(
            await self._session.scalars(
                select(VPNKey.key_fingerprint).where(VPNKey.key_fingerprint.in_(fingerprints))
            )
        )

        valid_rows: list[dict] = []
        errors: list[dict] = []
        for row_number, row in enumerate(raw_rows, start=1):
            row_errors: list[str] = []
            plan = plans.get(row["plan_code"])
            fingerprint = self._key_protector.fingerprint(row["config_text"]) if row["config_text"] else ""
            if plan is None:
                row_errors.append("plan_code does not exist")
            if row["key_type"] not in self.allowed_types:
                row_errors.append("unsupported key_type")
            if row["key_status"] not in self.allowed_statuses:
                row_errors.append("unsupported key_status")
            if not row["config_text"]:
                row_errors.append("config_text is required")
            elif fingerprint_counter[fingerprint] > 1:
                row_errors.append("duplicate key inside file")
            elif fingerprint in existing_fingerprints:
                row_errors.append("duplicate key already exists in DB")
            try:
                expires_at = datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None
            except ValueError:
                expires_at = None
                row_errors.append("expires_at must be ISO-8601")
            if row_errors:
                errors.append({"row_number": row_number, "label": row["label"], "errors": row_errors})
                continue
            valid_rows.append(
                {
                    **row,
                    "plan_id": plan.id,
                    "fingerprint": fingerprint,
                    "expires_at": expires_at,
                }
            )
        return {"rows_total": len(raw_rows), "valid_rows": valid_rows, "errors": errors}

    def _read_rows(self, content: bytes) -> list[dict]:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "typed-keys.sqlite"
            database_path.write_bytes(content)
            connection = sqlite3.connect(database_path)
            try:
                meta = connection.execute("SELECT format_version FROM export_meta LIMIT 1").fetchone()
                if not meta or meta[0] != self.format_version:
                    raise ValueError("Unsupported typed-key bundle version")
                columns = {row[1] for row in connection.execute("PRAGMA table_info(typed_keys)")}
                missing = self.required_columns - columns
                if missing:
                    raise ValueError(f"Missing typed_keys columns: {', '.join(sorted(missing))}")
                count = connection.execute("SELECT COUNT(*) FROM typed_keys").fetchone()[0]
                if count < 1 or count > 10_000:
                    raise ValueError("typed_keys must contain between 1 and 10000 rows")
                rows = connection.execute(
                    """
                    SELECT key_type, label, key_status, config_text, plan_code,
                           external_ref, comment, expires_at
                    FROM typed_keys ORDER BY rowid
                    """
                ).fetchall()
            except sqlite3.Error as exc:
                raise ValueError("Invalid typed-key SQLite bundle") from exc
            finally:
                connection.close()
        return [
            {
                "key_type": (row[0] or "").strip(),
                "label": (row[1] or "").strip(),
                "key_status": (row[2] or "").strip(),
                "config_text": (row[3] or "").strip(),
                "plan_code": (row[4] or "").strip(),
                "external_ref": (row[5] or "").strip() or None,
                "comment": (row[6] or "").strip() or None,
                "expires_at": (row[7] or "").strip() or None,
            }
            for row in rows
        ]
