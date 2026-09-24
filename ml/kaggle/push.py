"""Set the notebook's STAGE, write kernel-metadata.json, and push to Kaggle.

    python push.py index
    python push.py crops
    ...
    python push.py export

Each push gets its own kernel id (a kernel cannot mount its own output), and
`kernel_sources` is set to the immediately-preceding stage so the chain carries
/manifest.csv, crops/, fstats/ and weights/ forward automatically.

    python push.py spatial --no-push     # write the files, skip the push
    python push.py crops --owner someoneelse
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NOTEBOOK = HERE / "deeptrace_kaggle_train.ipynb"
METADATA = HERE / "kernel-metadata.json"

ORDER = ["index", "bench", "crops", "spatial", "temporal", "frequency", "fusion",
         "eval", "export"]
PREFIX = "deeptrace"
DEFAULT_DATASETS = ["xdxd003/ff-c23", "reubensuju/celeb-df-v2"]


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=ORDER)
    ap.add_argument("--owner", default="kambleom", help="Kaggle username (default: kambleom)")
    ap.add_argument("--dataset-sources", nargs="*", default=DEFAULT_DATASETS,
                    help="owner/dataset slugs to mount (default: ff-c23 + celeb-df-v2)")
    ap.add_argument("--competition-sources", nargs="*", default=[],
                    help='e.g. deepfake-detection-challenge (accept its rules first)')
    ap.add_argument("--no-push", action="store_true", help="only write the files")
    ap.add_argument("--no-gpu", action="store_true",
                    help="set enable_gpu false. Use for 'crops': face detection is "
                         "CPU-bound, so the GPU buys nothing and the CPU runtime is "
                         "unmetered whereas GPU hours are capped.")
    return ap.parse_args()


def set_stage_in_notebook(stage: str) -> None:
    nb = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    pattern = re.compile(r'^(\s*STAGE\s*=\s*)"[^"]*"')
    hits = 0
    for cell in nb["cells"]:
        if cell["cell_type"] != "code":
            continue
        src = cell["source"]
        for i, line in enumerate(src):
            if not pattern.match(line):
                continue
            # rewrite even when the value is unchanged, otherwise re-running the
            # same stage reports "found 0"
            src[i] = pattern.sub(lambda m: m.group(1) + '"%s"' % stage, line)
            hits += 1
    if hits != 1:
        sys.exit("expected exactly one STAGE assignment in the notebook, found %d" % hits)
    NOTEBOOK.write_text(json.dumps(nb, indent=1), encoding="utf-8")
    print("notebook  : STAGE = %r" % stage)


def kernel_id_for(owner: str, stage: str) -> str:
    return "%s/%s-%s" % (owner, PREFIX, stage)


def previous_kernel_id(args):
    idx = ORDER.index(args.stage)
    return kernel_id_for(args.owner, ORDER[idx - 1]) if idx > 0 else None


def previous_stage_ready(prev: str):
    """Is the stage we chain onto actually finished?

    'kaggle kernels push' mounts a source kernel's /kaggle/working only once that
    version is COMMITTED. Push while the previous stage is still training and the
    mount resolves to nothing: this stage then dies minutes later with "the earlier
    stage's output was not mounted", which looks like a code bug but is purely a
    race. Returns (ok, detail); ok is None when the state cannot be determined.
    """
    exe = shutil.which("kaggle")
    if exe is None:
        return None, "kaggle CLI not found"
    r = subprocess.run([exe, "kernels", "status", prev],
                       capture_output=True, text=True)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if "COMPLETE" in out:
        return True, "COMPLETE"
    for state in ("RUNNING", "QUEUED", "ERROR", "CANCEL"):
        if state in out:
            return False, state
    return None, out or "unknown"


def write_metadata(args) -> str:
    kernel_id = kernel_id_for(args.owner, args.stage)
    prev = previous_kernel_id(args)

    md = {
        "id": kernel_id,
        "title": "%s %s" % (PREFIX, args.stage),
        "code_file": NOTEBOOK.name,
        "language": "python",
        "kernel_type": "notebook",
        "is_private": True,
        "enable_gpu": not args.no_gpu,
        "enable_tpu": False,
        "enable_internet": True,
        "machine_shape": "",
        "dataset_sources": args.dataset_sources,
        "competition_sources": args.competition_sources,
        "kernel_sources": [prev] if prev else [],
        "model_sources": [],
    }
    METADATA.write_text(json.dumps(md, indent=2) + "\n", encoding="utf-8")
    print("metadata  : id=%s  kernel_sources=%s" % (kernel_id, md["kernel_sources"]))
    return kernel_id


def main():
    args = parse_args()
    if not NOTEBOOK.exists():
        sys.exit("not found: %s" % NOTEBOOK)

    prev = previous_kernel_id(args)
    if prev and not args.no_push:
        ok, state = previous_stage_ready(prev)
        print("preflight : %s -> %s" % (prev, state))
        if ok is False:
            sys.exit(
                "\nRefusing to push: %s is %s, not COMPLETE.\n"
                "Kaggle mounts a source kernel's /kaggle/working only after that\n"
                "version is committed. Pushing now mounts an EMPTY /kaggle/input and\n"
                "this stage fails minutes later with 'the earlier stage's output was\n"
                "not mounted'.\n"
                "Wait for it to finish, then re-run this command." % (prev, state))
        if ok is None:
            print("          (state unknown - continuing anyway)")

    set_stage_in_notebook(args.stage)
    kernel_id = write_metadata(args)

    if args.no_push:
        print("(skipped push)")
        return

    exe = shutil.which("kaggle")
    if exe is None:
        sys.exit("could not find the 'kaggle' CLI on PATH. Install it with "
                 "'pip install kaggle' and put its Scripts folder on PATH.")
    print("pushing   : %s kernels push -p %s" % (exe, HERE))
    r = subprocess.run([exe, "kernels", "push", "-p", str(HERE)], cwd=str(HERE))
    if r.returncode != 0:
        sys.exit(r.returncode)
    print()
    print("follow it : kaggle kernels status %s" % kernel_id)
    print("get files : kaggle kernels output %s -p ./out --file-pattern "
          '"(deeptrace_weights\\.zip|eval\\.md|manifest\\.csv|crop_index\\.csv)"' % kernel_id)


if __name__ == "__main__":
    main()
