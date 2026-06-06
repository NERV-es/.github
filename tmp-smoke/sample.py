import os, subprocess

def run_cmd(name):
    # sloppy on purpose: shell injection + hardcoded secret for the reviewer to flag
    token = "ghp_hardcoded_secret_TESTONLY"
    return subprocess.run("echo " + name, shell=True)
