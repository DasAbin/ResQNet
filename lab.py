"""Small send/status helper for the trusted-lab socket nodes.

Wraps node.Store so a live demo does not need inline Python one-liners:

    python3 lab.py send --db /tmp/resq-a.db --node A --to B \
        --body "Synthetic test SOS" --priority 2
    python3 lab.py status --db /tmp/resq-a.db --node A --transfers 10

Same trusted-lab scope as node.py: synthetic messages only, isolated
networks, no real distress traffic.
"""

import argparse
import secrets
import sys
import time

from node import ProtocolError, Store


def _send(args) -> int:
    msg_id = args.id or f"{args.node}-{int(time.time())}-{secrets.token_hex(3)}"
    message = {
        "id": msg_id,
        "source": args.node,
        "destination": args.to,
        "body": args.body,
        "priority": args.priority,
        "expires_at": time.time() + args.ttl,
    }
    try:
        result = Store(args.db, args.node).receive(message)
    except (ProtocolError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"{result} {msg_id}")
    return 0 if result in ("stored", "delivered", "duplicate") else 1


def _status(args) -> int:
    try:
        store = Store(args.db, args.node)
        info = store.status()
        transfers = store.transfers(args.transfers)
    except (ProtocolError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"node {info['node']}: pending {info['pending']}, "
          f"delivered {info['delivered']}, capacity {info['capacity']}")
    for row in transfers:
        when = time.strftime("%H:%M:%S", time.localtime(row["ts"]))
        detail = f" ({row['detail']})" if row.get("detail") else ""
        print(f"  {when} {row['direction']} {row['message_id']} "
              f"peer={row['peer']} {row['result']}{detail}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="ResQNet lab send/status helper")
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", required=True, help="node store path")
    common.add_argument("--node", required=True, help="own node id")
    send = sub.add_parser("send", parents=[common], help="inject a synthetic message")
    send.add_argument("--to", required=True, help="destination node id")
    send.add_argument("--body", required=True, help="message text (synthetic only)")
    send.add_argument("--priority", type=int, choices=[0, 1, 2], default=1,
                      help="0 routine, 1 important, 2 urgent/SOS (default 1)")
    send.add_argument("--ttl", type=float, default=3600.0,
                      help="seconds before the message expires (default 3600)")
    send.add_argument("--id", default=None, help="explicit message id (default: generated)")
    send.set_defaults(func=_send)
    status = sub.add_parser("status", parents=[common], help="show store state and transfer log")
    status.add_argument("--transfers", type=int, default=10,
                        help="number of recent transfer-log rows (default 10)")
    status.set_defaults(func=_status)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
