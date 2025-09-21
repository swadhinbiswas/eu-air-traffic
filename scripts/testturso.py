import os
import json
import ast
import asyncio
import time

from dotenv import load_dotenv
from libsql_client import create_client

load_dotenv()


def load_targets():
    raw = os.getenv("TURSO_TARGETS")

    if not raw:
        raise RuntimeError(
            "TURSO_TARGETS environment variable not found"
        )

    raw = raw.strip()

    try:
        return json.loads(raw)

    except json.JSONDecodeError:
        try:
            return ast.literal_eval(raw)

        except Exception as e:
            print("\n=== FAILED TO PARSE TURSO_TARGETS ===")
            print(repr(raw))
            raise RuntimeError(
                f"Could not parse TURSO_TARGETS: {e}"
            )


async def check_database(target):
    name = target.get("name", "unknown")
    url = target.get("url")
    token = target.get("token")

    if not url:
        print(f"\n❌ {name}")
        print("   Missing 'url' field")
        return

    if not token:
        print(f"\n❌ {name}")
        print("   Missing 'token' field")
        return

    start = time.perf_counter()

    if url.startswith("libsql://"):
        url = "https://" + url[len("libsql://"):]

    client = create_client(
        url=url,
        auth_token=token,
    )

    try:
        health = await client.execute(
            "SELECT 1 AS health"
        )

        version = await client.execute(
            "SELECT sqlite_version() AS version"
        )

        tables = await client.execute(
            """
            SELECT COUNT(*) AS table_count
            FROM sqlite_master
            WHERE type='table'
            """
        )

        latency_ms = round(
            (time.perf_counter() - start) * 1000,
            2,
        )

        print(f"\n✅ {name}")
        print(f"   URL      : {url}")
        print(f"   Health   : {health.rows[0]['health']}")
        print(f"   SQLite   : {version.rows[0]['version']}")
        print(f"   Tables   : {tables.rows[0]['table_count']}")
        print(f"   Latency  : {latency_ms} ms")

    except Exception as e:
        print(f"\n❌ {name}")
        print(f"   URL      : {url}")
        print(f"   Error    : {e}")

    finally:
        await client.close()


async def main():
    targets = load_targets()

    if not isinstance(targets, list):
        raise RuntimeError(
            "TURSO_TARGETS must be a list"
        )

    print("\n=== TURSO HEALTH CHECK ===")

    await asyncio.gather(
        *(check_database(t) for t in targets)
    )

    print("\n=== COMPLETE ===")


if __name__ == "__main__":
    asyncio.run(main())
