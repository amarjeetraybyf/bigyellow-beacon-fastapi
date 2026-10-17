"""
UUIDv7 implementation according to RFC 9562.
Uses 48-bit millisecond timestamp + 12-bit rand_a + 2-bit variant + 62-bit rand_b.
Monotonically sortable by time.
"""

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    """Generate a UUIDv7 (RFC 9562) using current timestamp in milliseconds."""
    nanoseconds = time.time_ns()
    timestamp_ms = nanoseconds // 1_000_000
    rand_bytes = os.urandom(10)

    # 48-bit timestamp
    time_high = (timestamp_ms >> 16) & 0xFFFFFFFF
    time_mid = timestamp_ms & 0xFFFF

    # 12-bit rand_a with version 7 (0111)
    rand_a = int.from_bytes(rand_bytes[:2], "big") & 0x0FFF
    time_hi_and_version = 0x7000 | rand_a

    # 62-bit rand_b with variant 2 (10xx)
    rand_b_int = int.from_bytes(rand_bytes[2:], "big")
    clock_seq_hi_and_reserved = 0x80 | ((rand_b_int >> 56) & 0x3F)
    clock_seq_low = (rand_b_int >> 48) & 0xFF
    node = rand_b_int & 0xFFFFFFFFFFFF

    return uuid.UUID(
        fields=(time_high, time_mid, time_hi_and_version, clock_seq_hi_and_reserved, clock_seq_low, node)
    )


def uuid7_str() -> str:
    """Generate UUIDv7 string."""
    return str(uuid7())
