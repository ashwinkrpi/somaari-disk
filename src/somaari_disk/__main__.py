"""Entry point: the UI window ([--demo] [--debug] [--reset-window]),
or `scan <path>` / `volumes` / `bench`."""

import sys


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["scan"]:
        from .cli import main as cli_main

        return cli_main(args[1:])
    if args[:1] == ["bench"]:
        from .cli import bench_main

        return bench_main(args[1:])
    if args[:1] == ["volumes"]:
        from .cli import volumes_main

        return volumes_main(args[1:])

    unknown = [a for a in args if a not in ("--demo", "--debug", "--reset-window")]
    if unknown:
        sys.stderr.write(f"unknown arguments: {' '.join(unknown)}\n")
        return 2
    from .app import run

    return run(
        demo="--demo" in args, debug="--debug" in args, reset_window="--reset-window" in args
    )


if __name__ == "__main__":
    sys.exit(main())
