"""Module entry point so `python -m flyread` works."""
from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())
