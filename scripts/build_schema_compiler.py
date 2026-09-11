#!/usr/bin/env python3
"""Build and test the pinned native compiler, optionally producing its wheel.

Uses a new disposable directory. No model, GPU, service or credentials needed.
Dependencies: git, CMake >=3.18, a C++17 compiler; wheel builds also need the
Python build dependencies already present in the pinned serving-image builder.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
REVISION = "5b4e9ce9e72524037ae24ecd831b9b6604d2eb48"
VERSION = "0.2.1+vita3"


def run(*args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--wheel", action="store_true")
    parser.add_argument("--baseline", action="store_true", help="Reproduce failures without the correction")
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    if args.wheel and args.baseline:
        parser.error("A baseline run must not produce a candidate wheel")
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    if args.work_dir:
        work = args.work_dir.resolve()
        work.mkdir(parents=True, exist_ok=False)
    else:
        work = Path(tempfile.mkdtemp(prefix="vita-schema-compiler-"))
    print(f"Build artifacts: {work}", flush=True)
    source = work / "source"
    run("git", "clone", "--branch", "v0.2.1", "--depth", "1", "--recurse-submodules",
        "--shallow-submodules", "https://github.com/mlc-ai/xgrammar.git", str(source))
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    if head != REVISION:
        raise RuntimeError(f"Upstream tag drift: expected {REVISION}, got {head}")
    patch = ROOT / "docker/schema-compiler/xgrammar-0.2.1.patch"
    header = ROOT / "docker/schema-compiler/schema_conjunction.h"
    if not args.baseline:
        run("git", "apply", "--check", str(patch), cwd=source)
        run("git", "apply", str(patch), cwd=source)
        shutil.copy2(header, source / "cpp/schema_conjunction.h")
    for path in (ROOT / "tests/native").iterdir():
        if path.is_file():
            shutil.copy2(path, source / "tests/cpp" / path.name)
    build = source / "build-contract"
    build.mkdir()
    (build / "config.cmake").write_text(
        "set(XGRAMMAR_BUILD_PYTHON_BINDINGS OFF)\n"
        "set(XGRAMMAR_BUILD_CXX_TESTS ON)\nset(CMAKE_BUILD_TYPE Release)\n"
    )
    run("cmake", "-S", str(source), "-B", str(build))
    run("cmake", "--build", str(build), "-j", str(args.jobs))
    run(str(build / "xgrammar_test"))
    if args.wheel:
        project = source / "pyproject.toml"
        text = project.read_text()
        old = 'version = "0.2.1"'
        if text.count(old) != 1:
            raise RuntimeError("Unexpected upstream package version")
        project.write_text(text.replace(old, f'version = "{VERSION}"'))
        os.environ["CMAKE_BUILD_PARALLEL_LEVEL"] = str(args.jobs)
        run(sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
            "--wheel-dir", str(work / "wheels"), ".", cwd=source)
    manifest = {
        "upstream_revision": REVISION, "candidate_version": VERSION,
        "baseline": args.baseline,
        "patch_sha256": hashlib.sha256(patch.read_bytes()).hexdigest(),
        "header_sha256": hashlib.sha256(header.read_bytes()).hexdigest(),
        "validation": "native C++ suite passed; GPU inference not tested",
    }
    (work / "compiler-build.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
