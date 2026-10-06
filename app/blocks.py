"""Fixed blocks of bars and the series a chart can ask for (mkt-data's docs/phase-2.md, "Charts that grow").

A chart asks `/api/bars` for its series at one interval, one block at a time.
Blocks are fixed, so the same block is always the same URL and a finished
block can be kept by the browser:

| interval                | block             | id     | bars per series |
|-------------------------|-------------------|--------|-----------------|
| day                     | a calendar year   | `2026` | about 250       |
| week                    | a decade          | `2020` | about 520       |
| month, quarter, year    | a decade          | `2020` | 120, 40, 10     |

So a screenful is a handful of requests whatever the zoom: three years of
days is four blocks, fifteen years of weeks two or three, all of history in
months seven. A block holds whole periods: a week belongs to the decade its
Monday is in, so a week running from December into January is never split.

A series is an instrument by short name or alias (`UST-10Y-CMT`, its yield in
percent), `spread(LONG,SHORT)` (long minus short in basis points) or
`fly(WING,BODY,WING)` (2 x body minus both wings, in basis points). New kinds
of series are new expressions here, not new endpoints.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

Interval = Literal["day", "week", "month", "quarter", "year"]

FIRST = date(1962, 1, 1)
# A block is final once its last day is this far behind: H.15 publishes a day
# late, and a long weekend can hold a value back a few days more.
SETTLE_DAYS = 7


class BadRequest(ValueError):
    pass


@dataclass(frozen=True)
class Block:
    id: str
    start: date  # the first day of the block's first period
    end: date  # the last day of its last period


def _first_monday(year: int) -> date:
    jan1 = date(year, 1, 1)
    return jan1 + timedelta(days=(7 - jan1.weekday()) % 7)


def block(interval: Interval, block_id: str) -> Block:
    """The block `block_id` at `interval`; BadRequest if it isn't one."""
    if not re.fullmatch(r"\d{4}", block_id):
        raise BadRequest(f"a block is a year like {'2026' if interval == 'day' else '2020'}, not {block_id!r}")
    y = int(block_id)
    if interval == "day":
        return Block(block_id, date(y, 1, 1), date(y, 12, 31))
    if y % 10:
        raise BadRequest(f"a {interval} block is a decade's first year (like 2020), not {block_id!r}")
    if interval == "week":
        return Block(block_id, _first_monday(y), _first_monday(y + 10) - timedelta(days=1))
    return Block(block_id, date(y, 1, 1), date(y + 10, 1, 1) - timedelta(days=1))


def block_of(interval: Interval, d: date) -> str:
    """The id of the block a day's bar is in (what mkt-ui works out for itself)."""
    if interval == "day":
        return f"{d.year:04d}"
    if interval == "week":
        d -= timedelta(days=d.weekday())
    return f"{d.year - d.year % 10:04d}"


def is_final(b: Block, today: date) -> bool:
    return b.end + timedelta(days=SETTLE_DAYS) < today


# --- Series expressions ---

NAME = r"[A-Za-z0-9][A-Za-z0-9.\-]*"
CALL = re.compile(rf"^\s*(spread|fly)\(\s*({NAME}(?:\s*,\s*{NAME})*)\s*\)\s*$", re.IGNORECASE)
PLAIN = re.compile(rf"^\s*({NAME})\s*$")
ARITY = {"spread": 2, "fly": 3}


@dataclass(frozen=True)
class SeriesSpec:
    kind: Literal["yield", "spread", "fly"]
    names: tuple[str, ...]  # as asked; resolved to short names by the caller

    @property
    def unit(self) -> str:
        return "%" if self.kind == "yield" else "bp"


def parse(expr: str) -> SeriesSpec:
    if m := CALL.match(expr):
        kind = m.group(1).lower()
        names = tuple(n.strip() for n in m.group(2).split(","))
        if len(names) != ARITY[kind]:
            raise BadRequest(f"{kind}() takes {ARITY[kind]} instruments, not {len(names)}: {expr!r}")
        return SeriesSpec(kind, names)  # type: ignore[arg-type]
    if m := PLAIN.match(expr):
        return SeriesSpec("yield", (m.group(1),))
    raise BadRequest(f"a series is a short name, spread(LONG,SHORT) or fly(WING,BODY,WING), not {expr!r}")


def key(kind: str, names: list[str]) -> str:
    """The series' canonical form, with resolved short names: what the UI keys it by."""
    return names[0] if kind == "yield" else f"{kind}({','.join(names)})"


def label(kind: str, names: list[str]) -> str:
    if kind == "yield":
        return names[0]
    if kind == "spread":
        return f"{names[0]} - {names[1]}"
    return f"2 x {names[1]} - {names[0]} - {names[2]}"
