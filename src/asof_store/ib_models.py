from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

type NewProduct = Literal["all", "T", "F"]


class IBRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    domain: str = "uk"
    new_product: NewProduct = Field(default="all", alias="newProduct")
    page_number: int = Field(default=1, ge=1, alias="pageNumber")
    page_size: int = Field(
        default=100,
        ge=100,
        le=500,
        multiple_of=100,
        alias="pageSize",
    )
    product_country: list[str] = Field(default_factory=list, alias="productCountry")
    product_symbol: str = Field(default="", alias="productSymbol")
    product_type: list[str] = Field(default_factory=list, alias="productType")
    sort_direction: Literal["asc", "desc"] = Field(
        default="asc",
        alias="sortDirection",
    )
    sort_field: str = Field(default="symbol", alias="sortField")


class InstrumentSummaryRequest(IBRequest):
    page_size: int = Field(
        default=100,
        ge=100,
        le=500,
        multiple_of=100,
        alias="pageSize",
    )
    product_type: list[str] = Field(
        min_length=1,
        default_factory=lambda: [
            "CMDTY",
            "FOP",
            "IOPT",
            "IND",
            "FUND",
            "FUT",
            "CASH",
            "OPT",
            "ETF",
            "WAR",
            "BOND",
            "STK",
            # "FC",
            # "CRYPTO",
        ],
        alias="productType",
    )


product_id2name = {
    # "CFD",
    "OPT": "Options",
    "FUND": "Mutual Funds",
    "FUT": "Futures",
    "STK": "Stocks",
    "BOND": "Bonds",
    "CMDTY": "Metals",
    "IND": "Indices",
    "WAR": "Warrants",
    "IOPT": "Structured Products",
    "CASH": "Currencies",
    "FOP": "Options on Futures",
    # "Cryptocurrency":"CRYPTO",
}

product_name2id = {v: k for k, v in product_id2name.items()}


class ProductsByFiltersRequest(IBRequest):
    page_size: int = Field(
        default=500,
        ge=100,
        le=500,
        multiple_of=100,
        alias="pageSize",
    )
    product_type: list[str] = Field(min_length=1, alias="productType")


class Exchange(BaseModel):
    model_config = ConfigDict(
        extra="allow",
        populate_by_name=True,
        json_schema_extra={"primary_key": ["id", "country_code"]},
    )

    id: str | None = None
    name: str
    country: str
    region: str
    assets: str
    country_code: str | None = None

    @property
    def primary_key(self) -> tuple[str, str] | None:
        """Return the composite key when both components are present."""
        if not self.id or not self.country_code:
            return None
        return self.id, self.country_code


class ExchangeResponse(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    exchanges: list[Exchange]
    product_type_count: dict[str, list[str]] = Field(alias="productTypeCount")
    product_count: int = Field(alias="productCount")


class InstrumentSummaryItem(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    product_type: str = Field(alias="productType")
    total_count: int = Field(strict=True, ge=0, alias="totalCount")


class Instrument(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    product_type: str | None = Field(default=None, alias="type")
    symbol: str | None = None
    exchange_id: str | None = Field(default=None, alias="exchangeId")
    local_symbol: str | None = Field(default=None, alias="localSymbol")
    description: str | None = None
    conid: int | None = Field(
        default=None,
        json_schema_extra={"primary_key": True},
    )
    under_conid: int | None = Field(default=None, alias="underConid")
    isin: str | None = None
    cusip: str | None = None
    currency: str | None = None
    country: str | None = None
    is_prime_exch_id: str | bool | None = Field(default=None, alias="isPrimeExchId")
    is_new_product: str | bool | None = Field(default=None, alias="isNewPdt")
    associated_entity_id: str | None = Field(default=None, alias="assocEntityId")
    fc_conid: int | None = None

    @property
    def primary_key(self) -> int | None:
        """Return IB's contract ID when the response supplies one."""
        return self.conid


class ProductsResponse(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    products: list[Instrument]


class FundProductsRequest(IBRequest):
    product_type: list[str] = Field(default_factory=lambda: ["FUND"], alias="productType")
    residency: str = ""
    family: str = ""
    is_fund_renamed: str = Field(default="", alias="isFundRenamed")
    txn_fee: str = Field(default="", alias="txnFee")
    investment_type: str = Field(default="", alias="iType")
    min_investment: str = Field(default="", alias="minInvestment")
    max_investment: str = Field(default="", alias="maxInvestment")
    identifier: str = ""
    account_type: str = Field(default="", alias="accountType")
    currency: str = ""


class Fund(BaseModel):
    model_config = ConfigDict(
        extra="allow",
        populate_by_name=True,
        json_schema_extra={"primary_key": "CONID"},
    )

    invest_type: str | None = Field(default=None, alias="INVEST_TYPE")
    assets: str | None = Field(default=None, alias="ASSETS")
    lt: str | None = Field(default=None, alias="LT")
    conid: int | None = Field(default=None, alias="CONID")
    expense_ratio: str | None = Field(default=None, alias="EXPENSE_RATIO")
    txn: str | None = Field(default=None, alias="TXN")
    sharpe: str | None = Field(default=None, alias="SHARPE")
    performance: str | None = Field(default=None, alias="PERF")
    isin: str | None = Field(default=None, alias="ISIN")
    initial_investment: str | None = Field(default=None, alias="INITIAL_INVEST")
    family: str | None = Field(default=None, alias="FAMILY")
    name: str | None = Field(default=None, alias="NAME")
    lipper: str | None = Field(default=None, alias="LIPPER")
    cusip: str | None = Field(default=None, alias="CUSIP")
    description_jp: str | None = Field(default=None, alias="DESCR_JP")
    symbol: str | None = Field(default=None, alias="SYMBOL")
    share_class: str | None = Field(default=None, alias="SHARE_CLASS")
    currency: str | None = Field(default=None, alias="ICUR")
    management_fee: str | None = Field(default=None, alias="MGTFEE")
    esg: str | None = Field(default=None, alias="ESG")
    exchange: str | None = Field(default=None, alias="EXCHANGE")
    fund_geography: str | None = Field(default=None, alias="FUND_GEO")

    @property
    def primary_key(self) -> int | None:
        """Return IB's contract ID when present."""
        return self.conid


class FundProductsResponse(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    total: int = Field(ge=0)
    funds: list[Fund]
