# Debugging the API in Docker (VS Code)

```bash
make debug      # same as `make up`, but the api runs under debugpy on :5678
```

Then in VS Code: set a breakpoint, run **Docker: attach to api** (F5), and call the API.
Execution stops at the breakpoint; stepping, the Variables pane, watches and the Debug Console
work as they do locally. Requires the Python Debugger extension (`ms-python.debugpy`).

- `.vscode/launch.json` maps your workspace folder to `/app`, which is the bind mount in
  `docker-compose.yml`, so breakpoints bind to your local files.
- The api starts with `runserver --noreload` (as VS Code's own Django config does), so one
  process lives for the whole session. After editing code, restart it:
  `docker compose -f docker-compose.yml -f docker-compose.debug.yml restart api`, then re-attach.
- Port 5678 is published on `127.0.0.1` only. debugpy has no authentication; never expose it.
- Only the api is debuggable this way. Tests run under pytest and can be debugged by attaching
  the same way to `python -m debugpy --listen 0.0.0.0:5678 --wait-for-client -m pytest ...`
  via `docker compose run --rm -p 127.0.0.1:5678:5678 api ...`.
