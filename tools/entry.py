"""PyInstaller entry point for the standalone Linux binary."""
import sys

from pi5gpio.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
