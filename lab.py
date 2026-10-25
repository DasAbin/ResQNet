"""Small send/status helper for the trusted-lab socket nodes.

Wraps node.Store so a live demo does not need inline Python one-liners:

    python3 lab.py send --db /tmp/resq-a.db --node A --to B \
        --body "Synthetic test SOS" --priority 2
    python3 lab.py status --db /tmp/resq-a.db --node A --transfers 10
    python3 lab.py claim --db /tmp/resq-b.db --node B --request <id>
    python3 lab.py track --db /tmp/resq-a.db --node A --request <id>

Same trusted-lab scope as node.py: synthetic messages only, isolated
networks, no real distress traffic.
"""

import argparse
import secrets
import sys
import time

from node import (CLAIM_PREFIX, RECEIPT_PREFIX, RESOLVE_PREFIX,
                  ProtocolError, Store, make_claim, make_resolve)


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


def _status_text(store: Store, limit: int) -> str:
    info = store.status()
    lines = [f"node {info['node']}: pending {info['pending']}, "
             f"delivered {info['delivered']}, capacity {info['capacity']}"]
    for row in store.transfers(limit):
        when = time.strftime("%H:%M:%S", time.localtime(row["ts"]))
        detail = f" ({row['detail']})" if row.get("detail") else ""
        lines.append(f"  {when} {row['direction']} {row['message_id']} "
                     f"peer={row['peer']} {row['result']}{detail}")
    return "\n".join(lines)


def _status(args) -> int:
    try:
        while True:
            print(_status_text(Store(args.db, args.node), args.transfers), flush=True)
            if not args.watch:
                return 0
            time.sleep(args.interval)
            print("---")
    except (ProtocolError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (KeyboardInterrupt, BrokenPipeError):
        return 0


def _find_request(store: Store, request_id: str) -> dict | None:
    return store.get(request_id)


def _lifecycle(args, make, verb: str) -> int:
    store = Store(args.db, args.node)
    request = _find_request(store, args.request)
    if request is None:
        print(f"error: request {args.request!r} not in this node's store", file=sys.stderr)
        return 2
    message = make(request, args.node)
    if message is None:
        print(f"error: cannot {verb} a system message id", file=sys.stderr)
        return 2
    result = store.receive(message)
    print(f"{result} {message['id']}")
    return 0 if result in ("stored", "delivered", "duplicate") else 1


def _claim(args) -> int:
    return _lifecycle(args, make_claim, "claim")


def _resolve(args) -> int:
    return _lifecycle(args, make_resolve, "resolve")


def _track(args) -> int:
    """Lifecycle of one request id as seen from THIS node's store.

    Distinct states: sent (still buffered here), received (destination
    receipt arrived), claimed (an operator accepted it), resolved (an
    operator marked it done). Received is not claimed; claimed is not
    resolved.
    """
    store = Store(args.db, args.node)
    delivered = {m["id"] for m in store.delivered_recent(500)}
    pending = {m["id"] for m in store.pending()}
    lines = [f"request {args.request} at node {args.node}:"]
    lines.append(f"  buffered here: {'yes' if args.request in pending else 'no'}")
    lines.append(f"  received (rcpt-): {'yes' if RECEIPT_PREFIX + args.request in delivered else 'no'}")
    lines.append(f"  claimed  (clm-):  {'yes' if CLAIM_PREFIX + args.request in delivered else 'no'}")
    lines.append(f"  resolved (res-):  {'yes' if RESOLVE_PREFIX + args.request in delivered else 'no'}")
    print("\n".join(lines))
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
    status.add_argument("--watch", action="store_true",
                        help="keep refreshing until Ctrl-C (every --interval seconds)")
    status.add_argument("--interval", type=float, default=2.0,
                        help="watch refresh interval in seconds (default 2)")
    status.set_defaults(func=_status)
    claim = sub.add_parser("claim", parents=[common],
                           help="claim a request this node holds (accepted, not resolved)")
    claim.add_argument("--request", required=True, help="request id to claim")
    claim.set_defaults(func=_claim)
    resolve = sub.add_parser("resolve", parents=[common],
                             help="mark a request this node holds as resolved")
    resolve.add_argument("--request", required=True, help="request id to resolve")
    resolve.set_defaults(func=_resolve)
    track = sub.add_parser("track", parents=[common],
                           help="show one request's lifecycle state at this node")
    track.add_argument("--request", required=True, help="request id to track")
    track.set_defaults(func=_track)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
