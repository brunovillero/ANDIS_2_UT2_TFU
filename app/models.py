from decimal import Decimal
from typing import Annotated
from pydantic import BaseModel, ConfigDict, Field

Money = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2, allow_inf_nan=False)]
Name = Annotated[str, Field(min_length=3, max_length=100)]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
