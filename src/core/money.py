from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, TypeAlias

from pydantic import Field
from sqlalchemy import Numeric

MONEY_PRECISION = 18
MONEY_SCALE = 2
MONEY_QUANTUM = Decimal("0.01")
ZERO_MONEY = Decimal("0.00")

MONEY_TYPE = Numeric(MONEY_PRECISION, MONEY_SCALE, asdecimal=True)
PERCENTAGE_TYPE = Numeric(7, 4, asdecimal=True)

Money: TypeAlias = Annotated[
    Decimal,
    Field(max_digits=MONEY_PRECISION, decimal_places=MONEY_SCALE),
]
NonNegativeMoney: TypeAlias = Annotated[
    Decimal,
    Field(ge=ZERO_MONEY, max_digits=MONEY_PRECISION, decimal_places=MONEY_SCALE),
]
PositiveMoney: TypeAlias = Annotated[
    Decimal,
    Field(gt=ZERO_MONEY, max_digits=MONEY_PRECISION, decimal_places=MONEY_SCALE),
]
Percentage: TypeAlias = Annotated[
    Decimal,
    Field(ge=Decimal("0"), le=Decimal("100"), max_digits=7, decimal_places=4),
]


def quantize_money(value: Decimal | int | str) -> Decimal:
    return Decimal(value).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
