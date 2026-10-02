"""The financial domain: branches, transaction categories, customers, accounts, loans, cards,
loan payments, statements, transactions and fraud flags (10 tables), with the reference data it
draws from (branch, merchant and transaction category names; the ZIP locations are the retail
domain's)."""

from __future__ import annotations

from shape_domains._packaged import PackagedDomain

SHAPE_API = "1.0"


class FinancialDomain(PackagedDomain):
    """``shape.domains`` entry ``financial``."""

    name = "financial"
    datasets = ("branch_names", "merchant_names", "transaction_categories")
    borrowed = {"us_zip_locations": ("retail", "us_zip_locations")}
