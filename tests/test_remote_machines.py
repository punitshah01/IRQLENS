from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from agent.main import AGENT_VERSION
from backend.app.main import REMOTE, STORE, app, settings
from backend.app.services.remote import LOADER, bootstrap, parse_target, ssh_command

# A browser on the server host: loopback peer, asking for the server by a loopback name.
LOCAL_BROWSER = {"base_url": "http://127.0.0.1:10000", "client": ("127.0.0.1", 50000)}
# What ssh starts on the machine, run here by this interpreter. -I -S: no site-packages, as on a machine with only Python.
CHILD = [sys.executable, "-I", "-S", "-u", "-c", LOADER]


def _wait(condition, timeout: float = 30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def _connection(machine_id: str):
    return next((item for item in REMOTE.describe() if item["id"] == machine_id), None)


@pytest.fixture
def child_instead_of_ssh(monkeypatch):
    monkeypatch.setattr(REMOTE, "_command", lambda machine: CHILD)
    monkeypatch.setattr(REMOTE, "_telemetry_interval", 0.2)
    monkeypatch.setattr(REMOTE, "retry_seconds", (0.2,))


def test_machine_addresses_are_validated_and_never_reach_a_shell():
    machine = parse_target(" Admin@Host-1.Example:2222 ", "server")
    assert (machine.id, machine.user, machine.host, machine.port, machine.role) == ("host-1.example:2222", "Admin", "host-1.example", 2222, "server")
    assert machine.target == "Admin@host-1.example:2222"
    plain = parse_target("192.0.2.35")
    assert (plain.id, plain.user, plain.port, plain.role, plain.target) == ("192.0.2.35", "", 22, "client", "192.0.2.35")
    for bad in ("", "-oProxyCommand=id", "root@-host", "host;id", "host name", "a@b@c", "host:0", "host:70000", "$(id)", "root@host`id`", "host/path", "root@", "local"):
        with pytest.raises(ValueError):
            parse_target(bad)
    with pytest.raises(ValueError):
        parse_target("host", "admin")

    command = ssh_command(parse_target("root@192.0.2.35:2222"))
    assert command[:4] == ["ssh", "-T", "-p", "2222"]
    # Options, then the user, then "--" and the host: nothing typed by a person can be read as an option.
    assert command[-5:] == ["-l", "root", "--", "192.0.2.35", f"python3 -u -c '{LOADER}'"]
    assert "'" not in LOADER
    for option in ("BatchMode=yes", "StrictHostKeyChecking=accept-new", "ForwardAgent=no", "ClearAllForwardings=yes"):
        assert option in command
    assert "-l" not in ssh_command(plain)


def test_streamed_agent_needs_only_python_and_ends_with_its_input():
    child = subprocess.Popen(CHILD, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        child.stdin.write(bootstrap({"sut_id": "stream-test", "role": "server", "telemetry_interval": 0.2}))
        child.stdin.flush()
        registration = json.loads(child.stdout.readline())
        assert (registration["type"], registration["sut_id"], registration["role"]) == ("register", "stream-test", "server")
        assert registration["agent_version"] == AGENT_VERSION and registration["hostname"]
        sample = json.loads(child.stdout.readline())
        assert sample["type"] == "telemetry" and sample["sut_id"] == "stream-test"
        assert {"system", "irq_rows", "softirq", "network_samples", "interfaces", "cpu_utilization", "storage", "pcie"} <= set(sample)

        # A capture's commands arrive on stdin; whatever else is written there is ignored.
        job = {"job_id": "job-1", "lease_token": "0" * 32, "categories": ["routes", "not-a-category"], "command_timeout_seconds": 2}
        child.stdin.write(b"not json\n" + json.dumps({"type": "diagnostics", "job": job}).encode() + b"\n")
        child.stdin.flush()
        result = None
        while not result or result["type"] != "diagnostics_result":
            result = json.loads(child.stdout.readline())
        assert (result["job_id"], result["sut_id"], result["lease_token"]) == ("job-1", "stream-test", "0" * 32)
        assert {row["category"] for row in result["results"]} == {"routes"}

        # Closing the connection is all it takes to stop it.
        child.stdin.close()
        assert child.wait(timeout=20) == 0
    finally:
        child.kill()
        child.stdout.close()
        child.stderr.close()


def test_machine_over_a_stream_is_monitored_and_captured(child_instead_of_ssh):
    machine_id = "stream-host"
    session_id = f"stream_capture_{time.time_ns()}"
    with TestClient(app, **LOCAL_BROWSER) as client:
        assert client.post("/api/machines", json={"target": f"tester@{machine_id}", "role": "client"}).status_code == 200
        try:
            _wait(lambda: _connection(machine_id)["state"] == "online")
            record = STORE.get_system(machine_id)
            # Stored under the address it was added by; shown by the name it reports.
            assert (record.mode, record.role, record.agent_version, record.status) == ("remote", "client", AGENT_VERSION, "ONLINE")
            assert record.hostname and record.hostname != machine_id
            _wait(lambda: STORE.latest_softirq(machine_id))
            listed = client.get("/api/systems").json()
            assert listed["ssh_machines"] == [
                {"id": machine_id, "target": f"tester@{machine_id}", "role": "client", "state": "online", "message": "", "hint": ""},
            ]
            box = next(machine for machine in client.get("/api/fleet").json()["machines"] if machine["id"] == machine_id)
            assert (box["status"], box["hostname"], box["connection"]["state"]) == ("ONLINE", record.hostname, "online")

            # A capture's commands travel down the same connection and the results come back on it.
            started = client.post("/api/sessions", json={"session_name": session_id, "duration_seconds": 30, "sut_id": machine_id, "categories": ["routes"]})
            assert started.status_code == 200
            jobs = _wait(lambda: [job for job in client.get(f"/api/sessions/{session_id}").json()["remote_diagnostics"] if job["status"] in ("completed", "failed")])
            assert len(jobs) == 1
            files = client.get(f"/api/sessions/{session_id}/files").json()["files"]
            assert any(item["category"] == "commands" and item["name"] == "status.json" for item in files)

            # Removing it ends the process on the other side; with a capture on record the machine stays listed, offline.
            process = REMOTE._machines[machine_id].process
            assert client.delete(f"/api/machines/{machine_id}").status_code == 200
            assert process.poll() is not None and _connection(machine_id) is None
            kept = STORE.get_system(machine_id)
            assert (kept.status, kept.last_seen) == ("OFFLINE", 0.0)
            assert machine_id not in {machine["id"] for machine in client.get("/api/fleet").json()["machines"]}

            # Without captures nothing is kept.
            assert client.delete(f"/api/sessions/{session_id}").status_code == 200
            assert client.post("/api/machines", json={"target": f"tester@{machine_id}"}).status_code == 200
            _wait(lambda: _connection(machine_id)["state"] == "online")
            assert client.delete(f"/api/machines/{machine_id}").status_code == 200
            assert STORE.get_system(machine_id) is None
        finally:
            REMOTE.remove(machine_id)
            client.delete(f"/api/sessions/{session_id}")
            STORE.delete_system(machine_id)


def test_samples_from_a_machine_with_a_wrong_clock_land_on_the_server_clock():
    from backend.app.main import CLOCK_SHIFTS, _remote_message
    from tests.test_remote_agent import _sample_registration, _sample_telemetry

    machine_id = "skewed-host"
    behind = 395.0

    def sample(moment: float) -> dict:
        telemetry = _sample_telemetry("its-own-name")
        for part in (telemetry, telemetry["system"], telemetry["softirq"], *telemetry["irq_rows"], *telemetry["network_samples"], *telemetry["interfaces"]):
            part["timestamp"] = moment
        return telemetry

    try:
        _remote_message(machine_id, {"type": "register", **_sample_registration("its-own-name")})
        started = time.time()
        for _ in range(3):
            _remote_message(machine_id, sample(time.time() - behind))
            time.sleep(0.2)
        # Stored under the address this server connected to, at the time this server saw them, evenly spaced.
        assert STORE.get_system(machine_id).hostname == "sut-host" and STORE.get_system("its-own-name") is None
        stamps = [row["timestamp"] for row in STORE.irq_rate_series(machine_id, started - 5)]
        assert len(stamps) == 3 and started - 0.5 <= stamps[0] and stamps[-1] <= time.time() + 0.5
        assert all(0.1 <= later - earlier <= 0.4 for earlier, later in zip(stamps, stamps[1:]))
        assert [row["timestamp"] for row in STORE.network_rate_series(machine_id, started - 5)] == stamps
        assert STORE.irq_rate_series(machine_id, started - behind - 60, started - behind + 60) == []
        # Its clock being set back does not send the samples into the past.
        _remote_message(machine_id, sample(time.time() - behind - 100))
        assert abs(STORE.latest_softirq(machine_id).timestamp - time.time()) < 1.0
    finally:
        CLOCK_SHIFTS.pop(machine_id, None)
        STORE.delete_system(machine_id)


def test_machine_that_cannot_be_reached_says_why(monkeypatch):
    refusal = "tester@refused-host: Permission denied (publickey,password)."
    monkeypatch.setattr(REMOTE, "_command", lambda machine: [sys.executable, "-c", f"import sys; sys.exit({refusal!r})"])
    monkeypatch.setattr(REMOTE, "retry_seconds", (60.0,))
    # Machines named on the command line are connected at startup; an entry that is not an address is skipped.
    monkeypatch.setattr(settings, "machines", ["server=tester@refused-host:2222", "client=-oProxyCommand=id", "boss=host"])
    with TestClient(app) as client:
        assert [item["id"] for item in REMOTE.describe()] == ["refused-host:2222"]
        state = _wait(lambda: _connection("refused-host:2222") if _connection("refused-host:2222")["state"] == "error" else None)
        assert state["message"] == refusal
        assert state["hint"].endswith("ssh-copy-id -p 2222 tester@refused-host")
        # It is on the map before it ever reported, with the reason.
        box = next(machine for machine in client.get("/api/fleet").json()["machines"] if machine["id"] == "refused-host:2222")
        assert (box["status"], box["role"], box["hostname"], box["connection"]["message"]) == ("ERROR", "server", "refused-host:2222", refusal)
        assert STORE.get_system("refused-host:2222") is None

        monkeypatch.setattr(REMOTE, "_command", lambda machine: ["irqlens-no-such-ssh-client"])
        REMOTE.add(parse_target("no-ssh-host"))
        missing = _wait(lambda: _connection("no-ssh-host") if _connection("no-ssh-host")["state"] == "error" else None)
        assert missing["message"].startswith("The IRQLENS server could not start ssh")
    # Stopping the server ends every connection.
    assert REMOTE.describe() == []


def test_only_a_trusted_caller_may_add_or_remove_machines(monkeypatch):
    monkeypatch.setattr(REMOTE, "_command", lambda machine: [sys.executable, "-c", "import sys; sys.exit('unreachable')"])
    monkeypatch.setattr(REMOTE, "retry_seconds", (60.0,))
    body = {"target": "tester@form-host", "role": "client"}

    # Another machine's browser: the dashboard is open to it, adding machines is not.
    with TestClient(app) as remote_browser:
        assert remote_browser.get("/api/systems").json()["can_manage_machines"] is False
        assert remote_browser.post("/api/machines", json=body).status_code == 403
        assert remote_browser.delete("/api/machines/form-host").status_code == 403
    with TestClient(app, base_url="http://127.0.0.1:10000", client=("198.51.100.7", 50000)) as named_loopback:
        assert named_loopback.post("/api/machines", json=body).status_code == 403

    with TestClient(app, **LOCAL_BROWSER) as browser:
        assert browser.get("/api/systems").json()["can_manage_machines"] is True
        # A loopback peer is not enough: a page of another site reaches 127.0.0.1 under its own name, and a
        # reverse proxy connects from loopback on behalf of everyone.
        for headers in (
            {"Host": "irqlens.example:10000"}, {"X-Forwarded-For": "198.51.100.7"}, {"X-Forwarded-Host": "irqlens.example"},
            {"X-Real-IP": "198.51.100.7"}, {"Forwarded": "for=198.51.100.7"}, {"Via": "1.1 proxy"},
        ):
            assert browser.get("/api/systems", headers=headers).json()["can_manage_machines"] is False
            assert browser.post("/api/machines", json=body, headers=headers).status_code == 403
        assert browser.post("/api/machines", json=body, headers={"Origin": "http://other-site.example"}).status_code == 403
        assert REMOTE.describe() == []

        for target in ("-oProxyCommand=id", "host;reboot", "root@host:99999", "local", "a" * 300):
            assert browser.post("/api/machines", json={"target": target}).status_code == 400
        assert browser.post("/api/machines", json={"target": "form-host", "role": "admin"}).status_code == 422
        assert browser.post("/api/machines", json={"target": ""}).status_code == 422

        added = browser.post("/api/machines", json=body, headers={"Origin": "http://127.0.0.1:10000"})
        assert added.status_code == 200 and added.json()["machine"]["id"] == "form-host"
        duplicate = browser.post("/api/machines", json=body)
        assert duplicate.status_code == 400 and "already" in duplicate.json()["detail"]
        assert [item["id"] for item in browser.get("/api/systems").json()["ssh_machines"]] == ["form-host"]
        assert browser.delete("/api/machines/form-host").status_code == 200
        assert browser.delete("/api/machines/form-host").status_code == 404
        assert browser.get("/api/systems").json()["ssh_machines"] == []
    with TestClient(app, base_url="http://localhost:10000", client=("::1", 50000)) as ipv6_browser:
        assert ipv6_browser.get("/api/systems").json()["can_manage_machines"] is True
        assert ipv6_browser.get("/api/systems", headers={"Host": "[::1]:10000"}).json()["can_manage_machines"] is True

    # With a token, holding it is what counts.
    monkeypatch.setattr(settings, "api_token", "manage-token")
    headers = {"Authorization": "Bearer manage-token"}
    with TestClient(app) as signed_in:
        assert signed_in.post("/api/machines", json=body).status_code == 401
        assert signed_in.get("/api/systems", headers=headers).json()["can_manage_machines"] is True
        assert signed_in.post("/api/machines", json=body, headers=headers).status_code == 200
        assert signed_in.delete("/api/machines/form-host", headers=headers).status_code == 200
