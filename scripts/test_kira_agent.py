#!/usr/bin/env python3
"""Interactive inspection through the existing production dispatcher harness."""
import sys
from test_kira_dispatch import main

if __name__ == '__main__':
    if len(sys.argv) == 1:
        sys.argv.append('--interactive')
    main()
