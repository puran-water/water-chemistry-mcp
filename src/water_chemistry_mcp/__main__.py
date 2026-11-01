"""Public stdio entrypoint using the packaged PHREEQC library by default."""
import os

def main():
    os.environ.setdefault("USE_PHREEQC_SUBPROCESS", "0")
    from .server import mcp
    mcp.run(transport="stdio")

if __name__ == "__main__":
    main()
