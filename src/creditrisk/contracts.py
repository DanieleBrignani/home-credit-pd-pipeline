"""Source data contracts for the Home Credit Default Risk files.

grain / pk / fk come from the Kaggle data description. `expected_rows` are the row counts of the
public competition files; a mismatch means the file is not the canonical one and ingestion fails
(override only with --allow-rowcount-mismatch, which is recorded in meta.ingest_batches).
`required` columns are what downstream dbt models read; extra columns are allowed (schema
evolution: additive changes are logged, removals/renames fail the contract).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TableContract:
    name: str
    file: str
    grain: str
    pk: tuple[str, ...]
    required: tuple[str, ...]
    expected_rows: int | None = None
    parent: str | None = None          # relationship description
    fk: tuple[str, ...] = field(default_factory=tuple)


CONTRACTS: dict[str, TableContract] = {c.name: c for c in [
    TableContract(
        "application_train", "application_train.csv", "one row per loan application (SK_ID_CURR)",
        ("SK_ID_CURR",),
        ("SK_ID_CURR", "TARGET", "NAME_CONTRACT_TYPE", "CODE_GENDER", "FLAG_OWN_CAR", "FLAG_OWN_REALTY",
         "CNT_CHILDREN", "AMT_INCOME_TOTAL", "AMT_CREDIT", "AMT_ANNUITY", "AMT_GOODS_PRICE", "NAME_INCOME_TYPE",
         "NAME_EDUCATION_TYPE", "NAME_FAMILY_STATUS", "NAME_HOUSING_TYPE", "REGION_POPULATION_RELATIVE",
         "DAYS_BIRTH", "DAYS_EMPLOYED", "DAYS_REGISTRATION", "DAYS_ID_PUBLISH", "OWN_CAR_AGE", "OCCUPATION_TYPE",
         "CNT_FAM_MEMBERS", "REGION_RATING_CLIENT_W_CITY", "EXT_SOURCE_1", "EXT_SOURCE_2", "EXT_SOURCE_3",
         "OBS_30_CNT_SOCIAL_CIRCLE", "DEF_30_CNT_SOCIAL_CIRCLE", "DAYS_LAST_PHONE_CHANGE",
         "AMT_REQ_CREDIT_BUREAU_YEAR"),
        307_511),
    TableContract(
        "application_test", "application_test.csv", "one row per unlabeled application (no TARGET)",
        ("SK_ID_CURR",), ("SK_ID_CURR", "AMT_CREDIT"), 48_744),
    TableContract(
        "bureau", "bureau.csv", "one row per credit at another institution reported to the bureau",
        ("SK_ID_BUREAU",),
        ("SK_ID_CURR", "SK_ID_BUREAU", "CREDIT_ACTIVE", "CREDIT_TYPE", "DAYS_CREDIT", "CREDIT_DAY_OVERDUE",
         "AMT_CREDIT_MAX_OVERDUE", "CNT_CREDIT_PROLONG", "AMT_CREDIT_SUM", "AMT_CREDIT_SUM_DEBT",
         "AMT_CREDIT_SUM_OVERDUE"),
        1_716_428, "N:1 -> application (SK_ID_CURR)", ("SK_ID_CURR",)),
    TableContract(
        "bureau_balance", "bureau_balance.csv", "one row per bureau credit per month",
        ("SK_ID_BUREAU", "MONTHS_BALANCE"), ("SK_ID_BUREAU", "MONTHS_BALANCE", "STATUS"),
        27_299_925, "N:1 -> bureau (SK_ID_BUREAU)", ("SK_ID_BUREAU",)),
    TableContract(
        "previous_application", "previous_application.csv", "one row per previous Home Credit application",
        ("SK_ID_PREV",),
        ("SK_ID_PREV", "SK_ID_CURR", "NAME_CONTRACT_TYPE", "AMT_APPLICATION", "AMT_CREDIT", "NAME_CONTRACT_STATUS",
         "DAYS_DECISION", "CNT_PAYMENT", "RATE_DOWN_PAYMENT", "NAME_PORTFOLIO", "CODE_REJECT_REASON"),
        1_670_214, "N:1 -> application (SK_ID_CURR)", ("SK_ID_CURR",)),
    TableContract(
        "installments_payments", "installments_payments.csv",
        "one row per (previous loan, installment number, version) payment record",
        ("SK_ID_PREV", "NUM_INSTALMENT_NUMBER", "NUM_INSTALMENT_VERSION", "DAYS_ENTRY_PAYMENT", "AMT_PAYMENT"),
        ("SK_ID_PREV", "SK_ID_CURR", "NUM_INSTALMENT_NUMBER", "DAYS_INSTALMENT", "DAYS_ENTRY_PAYMENT",
         "AMT_INSTALMENT", "AMT_PAYMENT"),
        13_605_401, "N:1 -> previous_application (SK_ID_PREV)", ("SK_ID_PREV", "SK_ID_CURR")),
    TableContract(
        "credit_card_balance", "credit_card_balance.csv", "one row per credit card (SK_ID_PREV) per month",
        ("SK_ID_PREV", "MONTHS_BALANCE"),
        ("SK_ID_PREV", "SK_ID_CURR", "MONTHS_BALANCE", "AMT_BALANCE", "AMT_CREDIT_LIMIT_ACTUAL",
         "AMT_DRAWINGS_ATM_CURRENT", "AMT_DRAWINGS_CURRENT", "SK_DPD", "SK_DPD_DEF"),
        3_840_312, "N:1 -> previous_application (SK_ID_PREV)", ("SK_ID_PREV", "SK_ID_CURR")),
    TableContract(
        "POS_CASH_balance", "POS_CASH_balance.csv", "one row per POS/cash loan (SK_ID_PREV) per month",
        ("SK_ID_PREV", "MONTHS_BALANCE"),
        ("SK_ID_PREV", "SK_ID_CURR", "MONTHS_BALANCE", "SK_DPD", "SK_DPD_DEF"),
        10_001_358, "N:1 -> previous_application (SK_ID_PREV)", ("SK_ID_PREV", "SK_ID_CURR")),
]}

# Columns that must never reach a model matrix (target, identifiers, post-outcome / future-dated fields).
FORBIDDEN_FEATURE_COLUMNS = frozenset({
    "TARGET", "SK_ID_CURR", "SK_ID_PREV", "SK_ID_BUREAU", "split",
    # previous_application: realised-outcome / post-decision timeline fields
    "DAYS_FIRST_DRAWING", "DAYS_FIRST_DUE", "DAYS_LAST_DUE_1ST_VERSION", "DAYS_LAST_DUE", "DAYS_TERMINATION",
    # bureau: realised end of credit
    "DAYS_ENDDATE_FACT",
    # protected attribute excluded on policy grounds
    "CODE_GENDER",
})
