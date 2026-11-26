"""Launch the standalone ManuCodeAI desktop application."""
from dotenv import load_dotenv

load_dotenv()

from desktop.app import main


if __name__ == "__main__":
    raise SystemExit(main())
