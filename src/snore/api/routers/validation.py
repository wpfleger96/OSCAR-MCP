from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from snore.api.deps import get_db
from snore.api.schemas import ValidationRequest
from snore.validation import BatchValidator, ValidationReport

router = APIRouter()


@router.post("/", response_model=ValidationReport)
def run_validation(
    body: ValidationRequest,
    db: Session = Depends(get_db),
) -> ValidationReport:
    validator = BatchValidator(db)
    return validator.validate_date_range(
        date_from=body.from_date.isoformat(),
        date_to=body.to_date.isoformat(),
        mode=body.mode,
    )
