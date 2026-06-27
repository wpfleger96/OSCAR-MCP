from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from snore.api.deps import get_db
from snore.validation import BatchValidator, ValidationReport

router = APIRouter()


class ValidationRequest(BaseModel):
    from_date: date
    to_date: date
    mode: str = "aasm"


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
