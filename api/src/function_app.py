"""Azure Functions entry points (Python v2 model). All logic lives in service.py."""
import logging

import azure.functions as func

import service

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)
try:
    SVC = service.from_env()
except Exception:  # misconfiguration: every route answers 503 instead of failing to start
    logging.getLogger("scorecard").exception("configuration error")
    SVC = None


def _call(name, req):
    if SVC is None:
        return func.HttpResponse('{"error":"unavailable"}', status_code=503, mimetype="application/json")
    status, headers, body = SVC.handle(name, req.method, dict(req.headers), req.get_body())
    return func.HttpResponse(body=body, status_code=status, headers=headers)


@app.route(route="v1/progress", methods=["POST", "OPTIONS"])
def progress(req: func.HttpRequest) -> func.HttpResponse:
    return _call("progress", req)


@app.route(route="v1/submit", methods=["POST", "OPTIONS"])
def submit(req: func.HttpRequest) -> func.HttpResponse:
    return _call("submit", req)


@app.route(route="v1/stats", methods=["GET"])
def stats(req: func.HttpRequest) -> func.HttpResponse:
    return _call("stats", req)


@app.route(route="dash", methods=["GET"])
def dash(req: func.HttpRequest) -> func.HttpResponse:
    return _call("dash", req)


@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    return _call("health", req)


@app.timer_trigger(schedule="*/30 * * * * *", arg_name="timer", run_on_startup=False, use_monitor=False)
def relay_drain(timer: func.TimerRequest) -> None:
    if SVC is not None:
        SVC.drain()
