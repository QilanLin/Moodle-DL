#!/usr/bin/env python3
"""Test escape sequence handling."""

import sys

# Test 1: Direct escape sequence
print("Test 1: Direct escape sequence")
print("\033[4A")
print("Done")

# Test 2: Using f-string like in the code
print("\nTest 2: Using f-string")
lines_printed = 4
print(f"\033[{lines_printed}A")
print("Done")

# Test 3: Check what \033 actually is
print(f"\nTest 3: Checking \\033")
print(f"repr('\\033'): {repr('\033')}")
print(f"ord('\\033'): {ord('\033')}")
print(f"hex(ord('\\033')): {hex(ord('\033'))}")

# Test 4: Print bytes to see what we're actually outputting
print("\nTest 4: Raw bytes output")
sys.stdout.buffer.write(b"\033[4A\n")
sys.stdout.flush()
print("Done after raw bytes")