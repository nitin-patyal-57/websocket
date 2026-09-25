# ============================================================
# TRANSACTION DATA (MOCK / SAMPLE)
# ============================================================
#
# In-memory placeholder transaction store, keyed by IMEI, so the
# assistant can answer real "what was my last transaction" and
# "give me a summary" questions - separate from the LED
# troubleshooting FAQ in training.py, which never touches real
# transaction amounts at all.
#
# Swap MOCK_TRANSACTIONS / get_transactions() for a real lookup (DB
# query, payments API call, etc.) once a live data source is wired
# up. Everything downstream (get_last_transaction,
# get_todays_transactions, the formatters) only depends on each
# transaction being a dict with amount/currency/status/payer/timestamp,
# so the rest of the pipeline doesn't need to change.
#
# Logic carried over verbatim from the reference MQTT server.py.

from datetime import datetime, timedelta

MOCK_TRANSACTIONS = {
    "861185084364857": [
        {"amount": 500.0, "currency": "INR", "status": "success", "payer": "Customer",
         "timestamp": datetime.now() - timedelta(minutes=5)},
        {"amount": 1250.0, "currency": "INR", "status": "success", "payer": "Customer",
         "timestamp": datetime.now() - timedelta(hours=1, minutes=20)},
        {"amount": 80.0, "currency": "INR", "status": "failed", "payer": "Customer",
         "timestamp": datetime.now() - timedelta(hours=3)},
        {"amount": 2000.0, "currency": "INR", "status": "success", "payer": "Customer",
         "timestamp": datetime.now() - timedelta(hours=5, minutes=45)},
        {"amount": 340.0, "currency": "INR", "status": "success", "payer": "Customer",
         "timestamp": datetime.now() - timedelta(days=1, hours=2)},
    ],
}


def get_transactions(imei):
    """Return this device's transactions, most recent first."""
    txns = MOCK_TRANSACTIONS.get(imei, [])
    return sorted(txns, key=lambda t: t["timestamp"], reverse=True)


def get_last_transaction(imei):
    """Return the single most recent transaction dict, or None."""
    txns = get_transactions(imei)
    return txns[0] if txns else None


def get_todays_transactions(imei):
    """Return today's transactions only, most recent first."""
    today = datetime.now().date()
    return [t for t in get_transactions(imei) if t["timestamp"].date() == today]


def format_amount(amount, currency="INR"):
    symbol = "\u20b9" if currency == "INR" else f"{currency} "
    if float(amount).is_integer():
        return f"{symbol}{int(amount):,}"
    return f"{symbol}{amount:,.2f}"


def format_transaction_announcement(txn):
    """Spoken-style sentence describing a single transaction."""
    if txn is None:
        return "You don't have any recorded transactions yet."

    amount_str = format_amount(txn["amount"], txn["currency"])
    time_str = txn["timestamp"].strftime("%I:%M %p").lstrip("0")

    if txn["status"] != "success":
        return f"Your last transaction of {amount_str} at {time_str} was not successful."

    return f"Your last transaction was {amount_str}, received at {time_str}."


def format_transaction_summary(imei, period="today"):
    """Spoken-style sentence summarizing recent transactions."""
    txns = get_todays_transactions(imei) if period == "today" else get_transactions(imei)
    successful = [t for t in txns if t["status"] == "success"]

    if not successful:
        return (
            "You have not received any successful transactions today."
            if period == "today"
            else "You don't have any recorded transactions yet."
        )

    total = sum(t["amount"] for t in successful)
    currency = successful[0]["currency"]
    count_word = "transaction" if len(successful) == 1 else "transactions"
    period_word = "today" if period == "today" else "overall"

    return (
        f"You have received {len(successful)} {count_word} {period_word}, "
        f"totaling {format_amount(total, currency)}."
    )


# Keyword checks are intentionally simple (substring match on the raw
# transcript) rather than another fuzzy-match pass against training
# data - these are direct data lookups, not FAQ answers, so we want a
# cheap, predictable trigger rather than a similarity score.
_SUMMARY_KEYWORDS = (
    "summary", "total sales", "how much did i receive", "how much did i earn",
    "how many transactions", "today's transactions", "todays transactions",
    "total transactions", "how much money", "total amount",
)
_LAST_TXN_KEYWORDS = (
    "last transaction", "last payment", "last sale", "recent transaction",
    "recent payment", "what was my last", "play my last", "play last transaction",
)


def detect_transaction_query(user_text):
    """
    Check whether the transcript is asking for real transaction data
    (as opposed to an LED troubleshooting question).

    Returns "summary", "last_transaction", or None.
    """
    if not user_text:
        return None

    text = user_text.strip().lower()

    if any(k in text for k in _SUMMARY_KEYWORDS):
        return "summary"
    if any(k in text for k in _LAST_TXN_KEYWORDS):
        return "last_transaction"

    return None
