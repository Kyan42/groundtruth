"""Scripted checks on the verifier (the gates).

A gate never uses what `groundtruth.py login` saved on this machine. With a saved login, every verify a gate starts
would go to the hosted verifier and upload its run to the hosted results store (on 2026-11-16 six gates put 18 test
runs on the hosted dashboard that way). So the settings file is pointed at one that does not exist, and a store or
token left in the shell's environment is dropped. A gate that wants a store sets GT_STORE itself, after this."""
import os
from pathlib import Path

os.environ["GT_CONFIG"] = str(Path(__file__).resolve().parent / "no-saved-login.json")
for _k in ("GT_STORE", "GT_STORE_TOKEN"):
    os.environ.pop(_k, None)
