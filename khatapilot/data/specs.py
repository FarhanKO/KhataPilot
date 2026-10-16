"""The parameters that describe a synthetic shop. Amounts are whole taka."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SupplierSpec:
    name: str
    number: str | None  # wallet number, bank account, or None for cash-only
    method: str  # bkash | nagad | bank | cash
    category: str  # ledger category for this supplier's goods
    cogs_share: float  # share of the shop's cost of goods bought from this supplier
    cycle_days: int  # days between deliveries
    first_delivery_weekday: int  # 0 = Monday
    terms_days: int  # 0 = paid on delivery; otherwise supplier credit
    aliases: tuple[str, ...] = ()  # other names the owner uses (short forms, Bangla script)


@dataclass(frozen=True)
class CustomerSpec:
    name: str
    number: str
    repay_days: float  # mean days to repay baki
    reliability: float  # probability a baki purchase is repaid at all
    weight: float  # relative share of the shop's baki sales


@dataclass(frozen=True)
class ExpenseSpec:
    name: str
    payee: str
    category: str
    amount: int
    day_of_month: int
    method: str


@dataclass(frozen=True)
class OneOffSpec:
    on: str  # ISO date
    kind: str  # owner_drawing | expense
    amount: int
    category: str
    note: str
    method: str


@dataclass
class ShopSpec:
    shop_id: str
    name: str
    owner_name: str
    shop_type: str  # grocery | pharmacy | mobile_recharge | tea_stall
    area: str
    district: str
    division: str
    urbanicity: str  # urban | semi_urban | rural
    history_days: int
    base_sales: float  # typical daily sales at the start of the history
    growth: float  # annual sales growth
    volatility: float  # daily log-noise standard deviation
    margin: float  # gross margin
    mobile_share: float  # share of sales paid by bKash/Nagad
    nagad_share: float  # share of mobile money that is Nagad
    baki_share: float  # share of sales on customer credit
    collect_rate: float  # share of baki eventually repaid
    supplier_on_time: float  # probability of paying a supplier on time when cash allows
    buffer_days: float  # cash the owner tries to keep, in days of sales
    drawings_rate: float  # owner's withdrawals as a share of weekly gross profit
    misc_expense_rate: float  # transport, packaging, tea etc. as a share of sales
    discipline: float  # hidden: the owner's financial discipline (0-1)
    shock: tuple[int, int, float] | None  # (start day index, length in days, sales drop)
    opening: dict[str, int]  # opening balances by method
    suppliers: list[SupplierSpec]
    customers: list[CustomerSpec]
    expenses: list[ExpenseSpec]
    owner_phone: str
    bkash_number: str
    nagad_number: str
    bank_account: str
    wallet_float: int = 2_000  # the owner tops bKash up from cash when it falls below this
    one_offs: list[OneOffSpec] = field(default_factory=list)
    tags: dict[str, str] = field(default_factory=dict)
