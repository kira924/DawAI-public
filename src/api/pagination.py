from typing import Annotated

from fastapi import Query

PageOffset = Annotated[
    int,
    Query(
        ge=0,
        description="Number of records to skip in the endpoint's documented stable order.",
    ),
]
PageLimit = Annotated[
    int,
    Query(
        ge=1,
        le=100,
        description="Maximum number of records to return.",
    ),
]
AuditPageLimit = Annotated[
    int,
    Query(
        ge=1,
        le=500,
        description="Maximum number of immutable audit records to return.",
    ),
]
