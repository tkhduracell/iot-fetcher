"""Read-only Docker introspection via the socket-proxy sidecar.

ai-brain never mounts ``docker.sock`` -- it talks plain HTTP to
``docker-proxy``, a sidecar that is the only thing with the socket and only
answers ``CONTAINERS``/``LOGS``/``TOP`` reads (see docker-compose.yml). A
restart is the one mutation the proxy allows through, and it is deliberately
not a tool here: it goes through ``propose`` -> Slack approval -> ``Executors``
like every other change to the real world, gated on a human reading the
container name in the Slack message rather than a fixed allowlist -- there is
no "restarting *that* is always safe" the way there is for HA services.
"""

from __future__ import annotations

from ai_brain.llm import ToolSpec
from ai_brain.tools import Tool, ToolContext, ToolRegistry, err, ok, wrap_external
from ai_brain.tools.http import decode_json, request, stream_tail

DOCKER_LOOPS = frozenset({"brain", "infra"})

MAX_CONTAINERS = 100
SOURCE = "docker"

# A container's log is free text written by whatever is running in it -- same
# category as an HA integration's log line, so it is fenced the same way
# before the model reads it.
LOG_TAIL_BYTES = 256 * 1024
DEFAULT_LOG_LINES = 50
MAX_LOG_LINES = 200


def _int_arg(args: dict, key: str, default: int, low: int, high: int) -> int:
    try:
        value = int(float(args.get(key, default)))
    except (TypeError, ValueError):
        return default
    return max(low, min(value, high))


async def _docker_ps(ctx: ToolContext, _args: dict) -> str:
    base = ctx.settings.docker_proxy_url.rstrip("/")
    url = f"{base}/containers/json"
    response, problem = await request(
        ctx, "GET", url, label="docker_ps", params={"all": "true"}
    )
    if problem is not None:
        return err(problem)

    body, problem = decode_json(response, "docker_ps")
    if problem is not None:
        return err(problem)
    if not isinstance(body, list):
        return err("docker_ps: backend returned a non-list body")

    containers = []
    for entry in body:
        if not isinstance(entry, dict):
            continue
        names = entry.get("Names") or []
        name = str(names[0]).lstrip("/") if names else str(entry.get("Id", ""))[:12]
        containers.append(
            {
                "name": name,
                "id": str(entry.get("Id", ""))[:12],
                "image": entry.get("Image"),
                "state": entry.get("State"),
                "status": wrap_external(SOURCE, str(entry.get("Status", ""))),
            }
        )

    return ok(
        {
            "containers": containers[:MAX_CONTAINERS],
            "truncated": len(containers) > MAX_CONTAINERS,
        }
    )


async def _docker_top(ctx: ToolContext, args: dict) -> str:
    name = str(args["container"])
    base = ctx.settings.docker_proxy_url.rstrip("/")
    url = f"{base}/containers/{name}/top"
    response, problem = await request(ctx, "GET", url, label="docker_top")
    if problem is not None:
        return err(problem)

    body, problem = decode_json(response, "docker_top")
    if problem is not None:
        return err(problem)
    if not isinstance(body, dict):
        return err("docker_top: backend returned a non-object body")

    titles = body.get("Titles") or []
    processes = body.get("Processes") or []
    rows = [
        dict(zip(titles, row, strict=False))
        for row in processes
        if isinstance(row, list)
    ]
    return ok({"processes": rows})


async def _docker_logs(ctx: ToolContext, args: dict) -> str:
    name = str(args["container"])
    wanted = _int_arg(args, "lines", DEFAULT_LOG_LINES, 1, MAX_LOG_LINES)
    base = ctx.settings.docker_proxy_url.rstrip("/")
    url = f"{base}/containers/{name}/logs"

    _, body, truncated, problem = await stream_tail(
        ctx,
        "GET",
        url,
        label="docker_logs",
        max_bytes=LOG_TAIL_BYTES,
        params={"stdout": "true", "stderr": "true", "tail": str(wanted)},
    )
    if problem is not None:
        return err(problem)

    lines = body.decode("utf-8", "replace").splitlines()
    if truncated and lines:
        lines = lines[1:]

    return ok(
        {
            "log": wrap_external(SOURCE, "\n".join(lines[-wanted:])),
            "returned": len(lines[-wanted:]),
            "truncated_to_tail": truncated,
        }
    )


def register_docker_tools(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            spec=ToolSpec(
                name="docker_ps",
                description=(
                    "List containers on the host (name, image, running state, status), "
                    "including stopped ones. Use to check whether a service is up, crash-"
                    "looping, or was recently restarted."
                ),
                parameters={"type": "object", "properties": {}},
            ),
            fn=_docker_ps,
            loops=DOCKER_LOOPS,
        )
    )
    registry.register(
        Tool(
            spec=ToolSpec(
                name="docker_top",
                description=(
                    "List the processes running inside a container, like `docker top`. "
                    "'container' is the name or id from docker_ps."
                ),
                parameters={
                    "type": "object",
                    "properties": {"container": {"type": "string"}},
                    "required": ["container"],
                },
            ),
            fn=_docker_top,
            loops=DOCKER_LOOPS,
        )
    )
    registry.register(
        Tool(
            spec=ToolSpec(
                name="docker_logs",
                description=(
                    "Read the tail of a container's stdout/stderr, for debugging a service "
                    "that is failing or restarting. 'container' is the name or id from "
                    "docker_ps. Returns the last 'lines' lines (default 50, max 200). Only "
                    "the last 256 KB of the log is searched."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "container": {"type": "string"},
                        "lines": {
                            "type": "integer",
                            "description": "How many lines to return (1-200).",
                        },
                    },
                    "required": ["container"],
                },
            ),
            fn=_docker_logs,
            loops=DOCKER_LOOPS,
        )
    )
