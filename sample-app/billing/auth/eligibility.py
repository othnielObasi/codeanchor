"""FROZEN for the compliance audit. Do not modify."""


def is_eligible(user) -> bool:
    return getattr(user, "refund_eligible", False)


def normalize_eligibility(user):
    return user.refund_eligible
