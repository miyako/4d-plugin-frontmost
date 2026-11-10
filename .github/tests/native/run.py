#!/usr/bin/env python3
"""Compile actual native sources plus the full SDK; never build a plugin bundle."""

import argparse
import hashlib
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SDK_SHA256 = "cbcab37e636e4fa2ad6793d419280f19261b0bf8cc8c3fe24747ea1a83229d0d"
CASES = ("path", "path-initial", "path-output", "bytes", "gray", "size",
         "thumbnail", "copy", "copy-empty", "handoffs", "arrays", "scalar",
         "charset", "failures", "dispatcher")
BASELINE_FAILURES = {
    "path": "double string disposal",
    "path-initial": "overwritten initial path string owner",
    "path-output": "double string disposal",
    "bytes": "picture balance",
    "gray": "picture balance",
    "size": "picture balance",
    "thumbnail": "picture balance",
    "copy": "AddressSanitizer: heap-buffer-overflow",
    "copy-empty": "string balance",
    "handoffs": "string balance",
    "failures": "AddressSanitizer: heap-buffer-overflow",
}

def check_preservation(root, ref, baseline):
    archive = subprocess.check_output(["git", "archive", ref], cwd=ROOT)
    exempt = set() if baseline else {"4D Plugin API/Classes/C_PICTURE.cpp", "README.md"}
    with tarfile.open(fileobj=io.BytesIO(archive)) as files:
        for member in files:
            if member.isfile() and member.name not in exempt:
                actual = root / member.name
                if not actual.is_file() or actual.read_bytes() != files.extractfile(member).read():
                    raise RuntimeError(f"unapproved change to archived source: {member.name}")
    print(f"PASS preservation against {ref} (exceptions={sorted(exempt)})", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--expect-baseline-failures", action="store_true")
    parser.add_argument("--preserve-ref", help="Compare every pre-existing file, except the intentional fix/docs")
    args = parser.parse_args()
    root = args.source.resolve()
    if args.preserve_ref:
        check_preservation(root, args.preserve_ref, args.expect_baseline_failures)
    sdk = root / "4D Plugin API"
    assert hashlib.sha256((sdk / "4DPluginAPI.c").read_bytes()).hexdigest() == SDK_SHA256
    with tempfile.TemporaryDirectory(prefix="frontmost-ownership-") as temp:
        binary = Path(temp) / "ownership"
        command = [
            "clang++", "-std=c++11", "-x", "objective-c++", "-g", "-O1",
            "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
            "-Wno-deprecated-declarations", "-Wno-unknown-pragmas",
            "-I", str(sdk), "-I", str(sdk / "Classes"),
            "-include", str(HERE / "platform.h"),
            str(sdk / "4DPluginAPI.c"),
            *(str(p) for p in sorted((sdk / "Classes").glob("*.cpp"))),
            str(root / "4DPlugin.cpp"), str(HERE / "ownership.mm"),
            "-framework", "Cocoa", "-framework", "Carbon", "-o", str(binary),
        ]
        subprocess.run(command, check=True)
        env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0:halt_on_error=1",
                   UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1")
        # macOS has no LSan; all shim-managed strings, pictures and handles are counted.
        for case in CASES:
            for representation in ("allocated", "null"):
                result = subprocess.run([str(binary), case, representation],
                                        env=env, text=True, capture_output=True)
                expected = BASELINE_FAILURES.get(case) if args.expect_baseline_failures else None
                # Null empty allocations cannot expose overwritten empty-owner leaks.
                if representation == "null" and case in ("gray", "size", "thumbnail", "copy-empty", "handoffs"):
                    expected = None
                if args.expect_baseline_failures and representation == "null":
                    if case == "path-initial":
                        expected = "double string disposal"
                    if case == "path-output":
                        expected = "picture balance"
                if expected:
                    if result.returncode == 0 or expected not in result.stderr:
                        raise RuntimeError(f"{case}/{representation}: expected {expected}\n{result.stdout}{result.stderr}")
                    if "AddressSanitizer" in expected and "CPicture::createCopy" not in result.stderr:
                        raise RuntimeError(f"{case}/{representation}: overread outside createCopy\n{result.stderr}")
                    print(f"EXPECTED BASELINE FAILURE {case}/{representation}: {expected}", flush=True)
                elif result.returncode:
                    raise RuntimeError(f"{case}/{representation} failed\n{result.stdout}{result.stderr}")
                else:
                    print(result.stdout.strip(), flush=True)


if __name__ == "__main__":
    main()
