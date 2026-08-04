from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from snore.api.deps import get_db
from snore.api.guards import RequireWritable
from snore.api.schemas import ValidationRequest
from snore.validation import BatchValidator, ValidationReport

router = APIRouter()


@router.post("/", response_model=ValidationReport)
async def run_validation(
    body: ValidationRequest,
    actor: RequireWritable,
    db: AsyncSession = Depends(get_db),
) -> ValidationReport:
    validator = BatchValidator(db, actor.profile_id)
    return await validator.validate_date_range(
        date_from=body.from_date.isoformat(),
        date_to=body.to_date.isoformat(),
        mode=body.mode,
    )
