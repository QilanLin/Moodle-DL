#!/usr/bin/env python3
"""
Minimal reproduction test for the terminal rendering bug.
This simulates the key difference between old and new versions.
"""

import sys
import time

def test_with_newline():
    """Simulate old version: print with newline (default end='\\n')"""
    print("\n=== Test 1: With newline (like old version) ===")
    time.sleep(1)
    
    # Initial menu
    print("Option 1")
    print("Option 2")
    print("Option 3")
    print("4 more lines below...")
    time.sleep(1)
    
    # Move cursor up
    print('\033[4A')  # No end='', just default newline
    time.sleep(0.5)
    
    # Redraw
    print('\033[KOption 1 (updated)')
    print('\033[KOption 2 (updated)')
    print('\033[KOption 3 (updated)')
    print('\033[K4 more lines below...')

def test_without_newline():
    """Simulate new version: print without newline (end='')"""
    print("\n=== Test 2: Without newline (like new version) ===")
    time.sleep(1)
    
    # Initial menu
    print("Option 1")
    print("Option 2")
    print("Option 3")
    print("4 more lines below...")
    time.sleep(1)
    
    # Move cursor up (with end='')
    print('\033[4A', end='', flush=True)
    time.sleep(0.5)
    
    # Redraw
    print('\033[KOption 1 (updated)')
    print('\033[KOption 2 (updated)')
    print('\033[KOption 3 (updated)')
    print('\033[K4 more lines below...')

def test_with_carriage_return():
    """Test adding \\r to return to line start"""
    print("\n=== Test 3: Without newline + \\r at start ===")
    time.sleep(1)
    
    # Initial menu
    print("Option 1")
    print("Option 2")
    print("Option 3")
    print("4 more lines below...")
    time.sleep(1)
    
    # Move cursor up (with end='')
    print('\r\033[4A', end='', flush=True)  # Add \r to return to line start
    time.sleep(0.5)
    
    # Redraw
    print('\033[KOption 1 (updated)')
    print('\033[KOption 2 (updated)')
    print('\033[KOption 3 (updated)')
    print('\033[K4 more lines below...')

if __name__ == "__main__":
    print("Testing terminal cursor behavior...")
    print("Press Enter to start test 1...")
    sys.stdin.readline()
    test_with_newline()
    
    print("\n\nPress Enter to start test 2...")
    sys.stdin.readline()
    time.sleep(1)
    test_without_newline()
    
    print("\n\nPress Enter to start test 3...")
    sys.stdin.readline()
    time.sleep(1)
    test_with_carriage_return()

    print("\n\nDone! Press Enter to exit...")
    sys.stdin.readline()
