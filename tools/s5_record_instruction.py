"""Record the IBKR order instruction created for an OrderInstructionRequest
(STAGE_5_0_ARCHITECTURE revision 5, §4.4).

    python -m tools.s5_record_instruction <request.json> --instruction-id ID --deep-link URL

The assistant creates the instruction in IBKR (connector, REVIEW_AND_APPROVE)
and gives you the id and link; this only writes them onto the request. It
makes no call to IBKR.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from app.e2e.stage5_order_instruction import OrderInstructionError, describe, record_instruction


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("request", type=Path)
    parser.add_argument("--instruction-id", required=True)
    parser.add_argument("--deep-link", required=True)
    args = parser.parse_args(argv)
    try:
        request = record_instruction(args.request, instruction_id=args.instruction_id, deep_link=args.deep_link,
                                     now=datetime.now(timezone.utc))
    except OrderInstructionError as exc:
        print(f"S5_ORDER_INSTRUCTION: NOT_RECORDED ({exc})")
        return 2
    for line in describe(request):
        print(line)
    print(f"instruction: {request.instruction_id} {request.deep_link}")
    print("S5_ORDER_INSTRUCTION: CREATED (awaiting the account holder's approval in IBKR)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
