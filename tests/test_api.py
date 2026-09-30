"""HTTP API 端到端测试：录入校验、求解、修订号过期、并发确认。"""

import threading

from app.sample import SAMPLE


def _put_sample(client):
    return client.put("/admin/dataset", json=SAMPLE.model_dump())


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_full_flow_solve_then_confirm(client):
    assert _put_sample(client).status_code == 200
    sol = client.post("/plans/solve")
    assert sol.status_code == 200, sol.text
    body = sol.json()
    assert body["max_occupied_tier"] == 2
    assert [a["container_no"] for a in body["assignments"]] == [
        "C001", "C002", "C003", "C004"]

    confirmed = client.post(
        f"/plans/{body['plan_revision']}/confirm",
        json={"plan_revision": body["plan_revision"],
              "slots_revision": body["slots_revision"],
              "containers_revision": body["containers_revision"]})
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"

    occ = {o["slot_id"]: o for o in client.get("/occupancies").json()}
    assert set(occ) == {a["slot_id"] for a in body["assignments"]}

    # 幂等：重复确认同一方案
    again = client.post(
        f"/plans/{body['plan_revision']}/confirm",
        json={"plan_revision": body["plan_revision"],
              "slots_revision": body["slots_revision"],
              "containers_revision": body["containers_revision"]})
    assert again.status_code == 200 and again.json()["status"] == "confirmed"


def test_invalid_weight_returns_400(client, dataset_payload):
    payload = dataset_payload()
    payload["containers"][0]["weight"] = -3
    r = client.put("/admin/dataset", json=payload)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_DATA"
    assert any("非法重量" in p for p in r.json()["error"]["details"])


def test_duplicate_container_no_returns_400(client, dataset_payload):
    payload = dataset_payload()
    payload["containers"][1]["container_no"] = payload["containers"][0]["container_no"]
    r = client.put("/admin/dataset", json=payload)
    assert r.status_code == 400 and "重复箱号" in str(r.json()["error"]["details"])


def test_duplicate_slot_id_and_bad_adjacency_400(client, dataset_payload):
    payload = dataset_payload()
    payload["slots"][1]["slot_id"] = payload["slots"][0]["slot_id"]
    r = client.put("/admin/dataset", json=payload)
    assert r.status_code == 400 and "槽位编号重复" in str(r.json()["error"]["details"])

    payload = dataset_payload()
    payload["adjacencies"].append({"slot_a": "A1", "slot_b": "ZZ"})
    r = client.put("/admin/dataset", json=payload)
    assert r.status_code == 400 and "无效槽位" in str(r.json()["error"]["details"])


def test_infeasible_plan_422_and_nothing_saved(client, dataset_payload):
    payload = dataset_payload(ship=(10_000, 20_000))  # 力矩不可能达到
    client.put("/admin/dataset", json=payload)
    r = client.post("/plans/solve")
    assert r.status_code == 422 and r.json()["error"]["code"] == "INFEASIBLE"
    assert client.get("/plans").json() == []


def test_stale_revision_rejected_and_plan_unchanged(client):
    _put_sample(client)
    sol = client.post("/plans/solve").json()

    # 资料被重新录入，修订号推进
    _put_sample(client)
    ds = client.get("/dataset").json()
    assert ds["slots_revision"] > sol["slots_revision"]

    # 用旧修订号确认：409，方案仍是 draft
    r = client.post(
        f"/plans/{sol['plan_revision']}/confirm",
        json={"plan_revision": sol["plan_revision"],
              "slots_revision": sol["slots_revision"],
              "containers_revision": sol["containers_revision"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "STALE_REVISION"
    plan = client.get(f"/plans/{sol['plan_revision']}").json()
    assert plan["status"] == "draft"
    assert client.get("/occupancies").json() == []


def test_concurrent_confirms_no_conflicting_occupancy(client):
    _put_sample(client)
    p1 = client.post("/plans/solve").json()
    # 第二套资料制造第二个同样占用 A1/B1/A2/B2 的草稿（修订号相同，同一份资料）
    p2 = client.post("/plans/solve").json()
    assert p1["plan_revision"] != p2["plan_revision"]

    results: dict[int, int] = {}
    barrier = threading.Barrier(2)

    def confirm(which):
        barrier.wait()
        plan = p1 if which == 1 else p2
        r = client.post(
            f"/plans/{plan['plan_revision']}/confirm",
            json={"plan_revision": plan["plan_revision"],
                  "slots_revision": plan["slots_revision"],
                  "containers_revision": plan["containers_revision"]})
        results[which] = r.status_code

    t1 = threading.Thread(target=confirm, args=(1,))
    t2 = threading.Thread(target=confirm, args=(2,))
    t1.start(); t2.start(); t1.join(); t2.join()

    statuses = sorted(results.values())
    assert statuses == [200, 409], results

    occ = client.get("/occupancies").json()
    slot_ids = [o["slot_id"] for o in occ]
    assert len(slot_ids) == len(set(slot_ids))  # 无冲突的重复占用
    assert len(occ) == 4


def test_revision_mismatch_between_path_and_body(client):
    _put_sample(client)
    sol = client.post("/plans/solve").json()
    r = client.post(f"/plans/{sol['plan_revision']}/confirm",
                    json={"plan_revision": sol["plan_revision"] + 1,
                          "slots_revision": sol["slots_revision"],
                          "containers_revision": sol["containers_revision"]})
    assert r.status_code == 400


def test_solve_excludes_confirmed_slots(client):
    _put_sample(client)
    sol = client.post("/plans/solve").json()
    client.post(f"/plans/{sol['plan_revision']}/confirm",
                json={"plan_revision": sol["plan_revision"],
                      "slots_revision": sol["slots_revision"],
                      "containers_revision": sol["containers_revision"]})
    # 槽位全部被 4 个箱占掉后，无新箱可配（空方案力矩 0，区间 -250..250 内可行）
    again = client.post("/plans/solve")
    assert again.status_code == 200
    assert again.json()["assignments"] == []
