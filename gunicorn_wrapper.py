#!/usr/bin/env python
import os
import sys
import subprocess


def main():
    # 1. Run migrations and setup
    try:
        subprocess.run(["python", "manage.py", "migrate", "--noinput"], check=False)
        subprocess.run(["python", "manage.py", "seed_defaults"], check=False)
        subprocess.run(["python", "manage.py", "import_college_directory"], check=False)
        if os.environ.get("ADMIN_INITIAL_PASSWORD"):
            subprocess.run(["python", "manage.py", "bootstrap_admin"], check=False)
    except Exception as exc:
        print(f"[gunicorn-wrapper] Startup notice: {exc}", file=sys.stderr)

    # 2. Resolve port safely
    port = os.environ.get("PORT", "8000")
    if not port.isdigit():
        port = "8000"

    # 3. Clean argv of unexpanded $PORT
    new_argv = [sys.argv[0]]
    has_bind = False

    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg in ("-b", "--bind"):
            has_bind = True
            new_argv.append(arg)
            i += 1
            if i < len(sys.argv):
                val = sys.argv[i].replace("$PORT", port).replace("${PORT}", port)
                new_argv.append(val)
        elif arg.startswith("--bind="):
            has_bind = True
            new_argv.append(arg.replace("$PORT", port).replace("${PORT}", port))
        else:
            new_argv.append(arg.replace("$PORT", port).replace("${PORT}", port))
        i += 1

    if not has_bind:
        new_argv.extend(["--bind", f"0.0.0.0:{port}"])

    sys.argv = new_argv
    print(f"[gunicorn-wrapper] Starting Gunicorn with args: {sys.argv[1:]}", flush=True)

    from gunicorn.app.wsgiapp import run

    sys.exit(run())


if __name__ == "__main__":
    main()

