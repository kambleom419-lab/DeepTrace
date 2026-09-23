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

ORDER = ["index", "crops", "spatial", "temporal", "frequency", "fusion", "eval", "export"]
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


def write_metadata(args) -> str:
    kernel_id = "%s/%s-%s" % (args.owner, PREFIX, args.stage)
    prev = None
    idx = ORDER.index(args.stage)
    if idx > 0:
        prev = "%s/%s-%s" % (args.owner, PREFIX, ORDER[idx - 1])

    md = {
        "id": kernel_id,
        "title": "%s %s" % (PREFIX, args.stage),
        "code_file": NOTEBOOK.name,
        "language": "python",
        "kernel_type": "notebook",
        "is_private": True,
        "enable_gpu": True,
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
