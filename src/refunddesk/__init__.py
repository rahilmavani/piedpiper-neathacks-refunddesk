"""RefundDesk: a demo customer-support agent for refund requests."""

__version__ = "0.1.0"


def main() -> None:
    from .eval import main as eval_main

    raise SystemExit(eval_main())
