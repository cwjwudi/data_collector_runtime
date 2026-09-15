#!/usr/bin/env python3
import os
import threading
from datetime import datetime, timezone

from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # Fix CORS issue

# Data storage
data_store = []

# OPC UA (lazy init)
_opcua_lock = threading.RLock()
_opcua_client = None
_opcua_server_url = None


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_opcua_client(server_url: str):
    """
    Get or (re)connect OPC UA client.
    Uses python-opcua (package name: opcua).
    """
    global _opcua_client, _opcua_server_url

    try:
        from opcua import Client  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError(
            "缺少依赖：opcua（python-opcua）。请先安装：pip install opcua"
        ) from e

    with _opcua_lock:
        # If URL changed, drop old connection
        if _opcua_client is not None and _opcua_server_url != server_url:
            try:
                _opcua_client.disconnect()
            except Exception:
                pass
            _opcua_client = None
            _opcua_server_url = None

        if _opcua_client is None:
            c = Client(server_url)
            c.connect()
            _opcua_client = c
            _opcua_server_url = server_url

        return _opcua_client


def _opcua_read(server_url: str, node_ids: list[str]) -> dict:
    client = _get_opcua_client(server_url)
    ts = _iso_now()
    out: dict[str, dict] = {}

    for node_id in node_ids:
        try:
            node = client.get_node(node_id)
            value = node.get_value()
            out[node_id] = {"value": value, "timestamp": ts}
        except Exception as e:
            # Try one reconnect on failures
            with _opcua_lock:
                try:
                    if _opcua_client is not None:
                        _opcua_client.disconnect()
                except Exception:
                    pass
                # force reconnect next call
                globals()["_opcua_client"] = None
                globals()["_opcua_server_url"] = None
            try:
                client = _get_opcua_client(server_url)
                node = client.get_node(node_id)
                value = node.get_value()
                out[node_id] = {"value": value, "timestamp": ts, "reconnected": True}
            except Exception as e2:
                out[node_id] = {"value": None, "timestamp": ts, "error": str(e2)}

    return out


@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        data = request.get_json()
        print(f"Received POST data: {data}")
        return jsonify({"status": "success", "data": data}), 200
    return "Flask Server Running"


# Postman push data (POST) / Frontend get data (GET)
@app.route('/data', methods=['GET', 'POST'])
def api_data():
    global data_store

    if request.method == 'POST':
        data = request.get_json()
        if data:
            if isinstance(data, list):
                data_store = data
            else:
                data_store.append(data)
            print(f"Received: {data}")
            return jsonify({"status": "success", "count": len(data_store)}), 200
        return jsonify({"status": "error"}), 400

    # GET - Frontend fetches data
    return jsonify(data_store), 200


@app.route('/opcua/read', methods=['GET', 'POST'])
def opcua_read():
    """
    Read OPC UA variable(s) by NodeId.

    - GET:  /opcua/read?serverUrl=opc.tcp://127.0.0.1:4840&nodeId=ns=2;s=Demo.Static.Scalar.Int32
            /opcua/read?nodeId=ns=2;s=A&nodeId=ns=2;s=B   (multiple nodeId)
    - POST: { "serverUrl": "...", "nodeIds": ["ns=2;s=A", "ns=2;s=B"] }
    """
    default_url = os.getenv("OPCUA_SERVER_URL", "opc.tcp://127.0.0.1:4840")

    try:
        if request.method == "GET":
            server_url = request.args.get("serverUrl", default_url)
            node_ids = request.args.getlist("nodeId")
        else:
            body = request.get_json(silent=True) or {}
            server_url = body.get("serverUrl", default_url)
            node_ids = body.get("nodeIds") or body.get("nodeId") or []

        if isinstance(node_ids, str):
            node_ids = [node_ids]

        if not node_ids or not all(isinstance(x, str) and x.strip() for x in node_ids):
            return (
                jsonify(
                    {
                        "status": "error",
                        "error": "nodeId/nodeIds 不能为空（例如：ns=2;s=Demo.Static.Scalar.Int32）",
                        "timestamp": _iso_now(),
                    }
                ),
                400,
            )

        result = _opcua_read(server_url, [x.strip() for x in node_ids])
        return jsonify({"status": "success", "serverUrl": server_url, "data": result}), 200
    except Exception as e:
        return jsonify({"status": "error", "error": str(e), "timestamp": _iso_now()}), 500


@app.route('/opcua/status', methods=['GET'])
def opcua_status():
    return jsonify(
        {
            "status": "success",
            "connected": _opcua_client is not None,
            "serverUrl": _opcua_server_url,
            "timestamp": _iso_now(),
        }
    )


if __name__ == '__main__':
    print("=" * 50)
    print("Flask Server Started")
    print("=" * 50)
    print("POST (Postman):  http://127.0.0.1:8000/data")
    print("GET  (Frontend): http://127.0.0.1:8000/data")
    print("OPC UA read (GET):  http://127.0.0.1:8000/opcua/read?nodeId=ns=2;s=Demo.Static.Scalar.Int32")
    print("OPC UA status:     http://127.0.0.1:8000/opcua/status")
    print("=" * 50)
    app.run(host='0.0.0.0', port=8000, debug=True)