"""读 GitHub Actions 的失败 annotation——**不需要 admin 权限**。

存在的理由：`/actions/runs/{id}/logs` 要 admin（返回 "Must have admin rights"），
而 annotation 是公开可读的。2026-07-31 起 CI 连红多次，定位全靠本地按 CI 的分组复跑
——那说明 CI 的失败输出当时不可用。配套改动是 `ci.yml` 把每条失败用例升成
`::error::`（只报组名等于「知道它红了，不知道哪红」）。

用法：
    python scripts/ci_annotations.py            # 最新一次 run
    python scripts/ci_annotations.py <run_id>   # 指定 run
"""
import io
import json
import subprocess
import sys

REPO = "SuperdeMan/cockpit-agent"
API = f"https://api.github.com/repos/{REPO}"


def get(url):
    out = subprocess.run(["curl", "-s", "-m", "40", url],
                         capture_output=True).stdout
    return json.loads(out.decode("utf-8", "replace"))


run_id = sys.argv[1] if len(sys.argv) > 1 else None
if not run_id:
    runs = get(f"{API}/actions/runs?per_page=1")["workflow_runs"][0]
    run_id = runs["id"]
    print(f"run #{runs['run_number']} {runs['status']} {runs['conclusion']}")

jobs = get(f"{API}/actions/runs/{run_id}/jobs").get("jobs", [])
for j in jobs:
    if j["conclusion"] != "failure":
        continue
    print(f"\nJOB {j['name']}")
    anns = get(f"{API}/check-runs/{j['id']}/annotations")
    for a in anns if isinstance(anns, list) else []:
        if a.get("annotation_level") == "failure":
            msg = (a.get("message") or "").replace("\n", " ")
            print("  ", msg[:200])
