from __future__ import annotations

from app.db.models import ImportBatch
from app.repositories.base import BaseRepository


class ImportBatchesRepository(BaseRepository):
    async def create(
        self,
        *,
        uploaded_by_user_id: int | None,
        source_filename: str,
        rows_total: int,
        rows_imported: int,
        rows_rejected: int,
        status: str,
        report_json: dict,
    ) -> ImportBatch:
        batch = ImportBatch(
            uploaded_by_user_id=uploaded_by_user_id,
            source_filename=source_filename,
            rows_total=rows_total,
            rows_imported=rows_imported,
            rows_rejected=rows_rejected,
            status=status,
            report_json=report_json,
        )
        self.session.add(batch)
        await self.session.flush()
        return batch

