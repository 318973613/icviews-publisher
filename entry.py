"""Application entry point; workers never construct a GUI."""
import sys


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        # Frozen Python ignores PYTHONUTF8; worker pipes use a fixed UTF-8 protocol.
        for stream in (sys.stdin, sys.stdout, sys.stderr):
            if stream is not None:
                stream.reconfigure(encoding="utf-8", errors="replace")
        if sys.stdout is not None:
            sys.stdout.reconfigure(line_buffering=True)
        if len(sys.argv) < 3:
            return 2
        channel, arguments = sys.argv[2], sys.argv[3:]
        sys.argv = [sys.argv[0], *arguments]
        if channel == "auth":
            import auth_worker
            return auth_worker.main()
        if channel == "publish":
            import publish
            return publish.main()
        return 2
    from desktop import main as gui_main
    gui_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
