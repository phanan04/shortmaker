import argparse

from .config import load_settings


def main():
    parser = argparse.ArgumentParser(description="Tu Tiên Shorts local backend")
    parser.add_argument("--config", default=None, help="Path to config.toml")
    args = parser.parse_args()
    settings = load_settings(args.config)
    import uvicorn
    from .app import create_app
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, workers=1)


if __name__ == "__main__":
    main()
