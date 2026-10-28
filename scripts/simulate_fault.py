"""
CLI tool for injecting controlled test faults into HealKube demo application.
Requires ENABLE_FAULT_INJECTION=true on target workload.
"""

import argparse
import sys
import requests


def main():
    parser = argparse.ArgumentParser(description="HealKube Demo Fault Injection Utility")
    parser.add_argument(
        "--endpoint",
        default="http://localhost:8000",
        help="Base URL of the demo application (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--type",
        choices=["500", "latency", "dependency", "reset", "status"],
        required=True,
        help="Fault type: 500 (HTTP errors), latency (delay), dependency (upstream outage), reset, status",
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=0.8,
        help="Error rate probability between 0.0 and 1.0 (for type=500)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="Extra latency in seconds (for type=latency)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help="Optional auto-expiration duration in seconds (0 = indefinite)",
    )

    args = parser.parse_args()
    base_url = args.endpoint.rstrip("/")

    try:
        if args.type == "status":
            resp = requests.get(f"{base_url}/fault/status", timeout=5)
            print("Current Fault Status:", resp.json())
            return

        if args.type == "reset":
            resp = requests.post(f"{base_url}/fault/reset", timeout=5)
            print("Fault Reset Response:", resp.json())
            return

        payload = {
            "error_rate": 0.0,
            "extra_latency_seconds": 0.0,
            "simulate_dependency_down": False,
            "duration_seconds": args.duration,
        }

        if args.type == "500":
            payload["error_rate"] = args.rate
            print(f"Injecting {args.rate * 100:.0f}% HTTP 500 errors...")
        elif args.type == "latency":
            payload["extra_latency_seconds"] = args.delay
            print(f"Injecting {args.delay:.2f}s latency...")
        elif args.type == "dependency":
            payload["simulate_dependency_down"] = True
            print("Simulating upstream dependency outage...")

        resp = requests.post(f"{base_url}/fault/inject", json=payload, timeout=5)
        if resp.status_code == 200:
            print("Success:", resp.json())
        else:
            print(f"Failed (HTTP {resp.status_code}):", resp.text)
            sys.exit(1)

    except requests.exceptions.RequestException as e:
        print(f"Connection error to {base_url}: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
