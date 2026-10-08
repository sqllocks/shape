"""The capital markets domain: exchanges, sectors, industries, companies, daily prices, dividends,
earnings, insider transactions, splits and trades (10 tables), with the reference data it draws
from (exchanges, GICS sectors, index memberships, S&P 500 constituents)."""

from __future__ import annotations

from shape_domains._packaged import PackagedDomain

SHAPE_API = "1.0"


class CapitalMarketsDomain(PackagedDomain):
    """``shape.domains`` entry ``capital_markets``."""

    name = "capital_markets"
    description = (
        "Capital Markets domain with companies, daily prices, dividends, earnings, insider trades, "
        "and tick-level trades"
    )
    datasets = ("exchanges", "gics_sectors", "index_memberships", "sp500_constituents")
