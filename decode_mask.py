#!/usr/bin/env python3
"""
decode_mask.py
==============
Quick standalone helper to translate a raw NTFS access mask into a
human-readable permission breakdown - the same logic used inside the
ACL comparison reports. Does not require Windows or pywin32.

USAGE
    python decode_mask.py 0x1E01FF
    python decode_mask.py 1966591

EXAMPLES
    python decode_mask.py 0x1F01FF
    -> Full Control (0x1F01FF)

    python decode_mask.py 0x1E01FF
    -> Full Control except: Delete (0x1E01FF)

    python decode_mask.py 0x120089
    -> Read (0x120089)
"""

import sys

import acl_compare_core as core


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)

    raw = sys.argv[1].strip()
    try:
        mask = int(raw, 16) if raw.lower().startswith("0x") else int(raw)
    except ValueError:
        print(f"ERROR: could not parse '{raw}' as a number (hex like 0x1E01FF or decimal).", file=sys.stderr)
        sys.exit(1)

    print(core.decode_mask_detailed(mask))


if __name__ == "__main__":
    main()
