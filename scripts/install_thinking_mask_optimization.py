#!/usr/bin/env python3
"""Install the pinned SGLang inactive strict-thinking mask optimization.

The patch is applied to the installed ``sglang`` package, not a source checkout.
Original and patched hashes are pinned so source drift fails closed, while a
second install on the already-patched package is an explicit no-op.
"""
import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


REVISION = "db272201a2dbd72e5699e443240a851f1313ad45"
PATCH_SHA256 = "3551f347239bdc6b3940ffca49bfe8a7684a807abbc3dfd30583f7353c8fa666"
FILES = {
    "srt/constrained/base_grammar_backend.py": (
        "41f57a8cb2eda3380cf903535cecb04b873b3abd94b6ad0fdfd2a430778c2e4a",
        "02e6a215a5904c0af7f2160b8362275a57fbc8361558502f200f939de9072365",
    ),
    "srt/constrained/reasoner_grammar_backend.py": (
        "ce606034a4b6ff68212ff54d5a8783ecd0f07b3881935708b4416d2c29773a40",
        "df7e3790d9094cfa5e200af30aa538086f1f1bb400955372108d5d9a159f93ef",
    ),
    "srt/sampling/sampling_batch_info.py": (
        "73d8747bf1e963323a553ec127e226a0817ba5691dba5c0f12259cfc1ccd2885",
        "ce982622f1d564b09552375291ef9b5089af6147a9dcb67f1f65185359d6b78a",
    ),
    "srt/speculative/spec_utils.py": (
        "f7b6db9995357dd99a221b763a642fda1a26b5ec21bc33cb7e350f2a5bcd6343",
        "0d98f61d94d985aeeede25883968906bad9a45fbc75277c1ac4074bf2388bcff",
    ),
}


class SourceDriftError(RuntimeError):
    """Raised when installed SGLang differs from the pinned revision."""


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_package_root(explicit):
    if explicit is not None:
        package_root = explicit.resolve()
    else:
        package = importlib.util.find_spec("sglang")
        if package is None or package.origin is None:
            raise RuntimeError("SGLang must be installed before applying the mask optimization")
        package_root = Path(package.origin).resolve().parent
    if package_root.name != "sglang":
        raise RuntimeError(f"Expected an installed sglang package root, got {package_root}")
    return package_root


def source_state(package_root):
    state = {}
    for relative_path, (original, patched) in FILES.items():
        path = package_root / relative_path
        if not path.is_file():
            raise SourceDriftError(f"Missing pinned SGLang file: {path}")
        digest = sha256(path)
        if digest == patched:
            state[relative_path] = "patched"
        elif digest == original:
            state[relative_path] = "original"
        else:
            raise SourceDriftError(
                f"Unexpected SGLang source for {relative_path}: {digest}"
            )
    return state


def _materialize_patch(package_root, patch_path):
    """Apply and verify the patch in a disposable tree with no parent Git repo."""
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git is required to apply the pinned thinking-mask patch")
    with tempfile.TemporaryDirectory(prefix="sglang-thinking-mask-") as tmp:
        temp_root = Path(tmp)
        temp_package = temp_root / "sglang"
        for relative_path in FILES:
            source = package_root / relative_path
            target = temp_package / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

        env = os.environ.copy()
        env["GIT_CEILING_DIRECTORIES"] = str(temp_root)
        subprocess.run(
            [git, "apply", "--check", "-p1", str(patch_path)],
            cwd=temp_root,
            env=env,
            check=True,
        )
        subprocess.run(
            [git, "apply", "-p1", str(patch_path)],
            cwd=temp_root,
            env=env,
            check=True,
        )
        if set(source_state(temp_package).values()) != {"patched"}:
            raise SourceDriftError("Disposable patch tree did not reach the pinned result")
        return {
            relative_path: (temp_package / relative_path).read_bytes()
            for relative_path in FILES
        }


def _replace_file(target, data):
    mode = target.stat().st_mode
    temporary = target.with_name(target.name + ".vita-tmp")
    try:
        temporary.write_bytes(data)
        os.chmod(temporary, mode)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def install(package_root, patch_path=None):
    package_root = resolve_package_root(package_root)
    if patch_path is None:
        patch_path = (
            Path(__file__).resolve().parents[1]
            / "docker/strict-thinking/sglang-db272201.patch"
        )
    patch_path = patch_path.resolve()
    if sha256(patch_path) != PATCH_SHA256:
        raise SourceDriftError(f"Unexpected optimization patch: {patch_path}")

    state = source_state(package_root)
    if set(state.values()) == {"patched"}:
        print(f"Thinking-mask optimization already installed at {package_root}")
        return False
    if set(state.values()) != {"original"}:
        raise SourceDriftError(f"Mixed SGLang source state: {state}")

    patched_files = _materialize_patch(package_root, patch_path)
    for relative_path, data in patched_files.items():
        _replace_file(package_root / relative_path, data)

    final_state = source_state(package_root)
    if set(final_state.values()) != {"patched"}:
        raise SourceDriftError(f"Patch did not reach the pinned result: {final_state}")
    print(f"Installed thinking-mask optimization at {package_root}")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", type=Path, help="Defaults to installed SGLang")
    parser.add_argument("--patch", type=Path, help="Defaults to the repository patch")
    args = parser.parse_args()
    install(args.package_root, args.patch)


if __name__ == "__main__":
    main()
