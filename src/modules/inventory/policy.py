from datetime import date

from fastapi import HTTPException, status

from src.core.business_time import ExpiryStatus, classify_expiry, local_business_date


def validate_incoming_expiry(
    expiry_date: date,
    *,
    confirm_near_expiry: bool,
    actor_role: str,
) -> ExpiryStatus:
    expiry_state = classify_expiry(expiry_date, as_of=local_business_date())
    if expiry_state == ExpiryStatus.EXPIRED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Expired stock cannot be received.",
        )
    if expiry_state == ExpiryStatus.CRITICAL:
        if not confirm_near_expiry:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Manager confirmation is required to receive stock expiring within 30 days.",
            )
        if actor_role != "manager":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only a manager can confirm receipt of stock expiring within 30 days.",
            )
    return expiry_state
