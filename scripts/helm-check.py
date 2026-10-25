#!/usr/bin/env python3
"""Run `helm lint` and `helm template` for every Helm source in apps/*.yaml.

Reads each Argo CD Application, finds sources that have a `chart`, resolves the
`$values/...` value files to paths in this repo, and renders the chart with them.
Needs helm and PyYAML. Run from the repo root.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

failed = False


def run(*cmd):
    global failed
    print("+", " ".join(cmd), flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        failed = True
        print(result.stdout, result.stderr, sep="\n", file=sys.stderr)


for app_file in sorted(Path("apps").glob("*.yaml")):
    app = yaml.safe_load(app_file.read_text())
    spec = app["spec"]
    sources = spec.get("sources") or [spec["source"]]
    for src in sources:
        if "chart" not in src:
            continue
        helm = src.get("helm", {})
        name = helm.get("releaseName", app["metadata"]["name"])
        namespace = spec["destination"]["namespace"]
        values = []
        for vf in helm.get("valueFiles", []):
            path = Path(vf.replace("$values/", "", 1))
            if not path.is_file():
                failed = True
                print(f"{app_file}: value file {path} does not exist", file=sys.stderr)
            values += ["-f", str(path)]

        print(f"\n== {app_file}: {src['chart']} {src['targetRevision']}")
        repo = src["repoURL"]
        if repo.startswith("http"):
            chart_ref = ["--repo", repo, src["chart"]]
        else:
            chart_ref = [f"oci://{repo}/{src['chart']}"]
        version = ["--version", src["targetRevision"]]

        with tempfile.TemporaryDirectory() as tmp:
            pull = subprocess.run(
                ["helm", "pull", *chart_ref, *version, "--untar", "--untardir", tmp],
                capture_output=True, text=True,
            )
            if pull.returncode != 0:
                failed = True
                print(pull.stderr, file=sys.stderr)
                continue
            chart_dir = str(Path(tmp) / src["chart"])
            run("helm", "lint", chart_dir, *values)
            run("helm", "template", name, chart_dir, "-n", namespace, "--include-crds", *values)

sys.exit(1 if failed else 0)
