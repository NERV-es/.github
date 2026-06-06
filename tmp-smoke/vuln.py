import os, subprocess, hashlib

API_TOKEN = "ghp_supersecrethardcoded0123456789ABCDEF"

def run_cmd(user_input):
    cmd = "echo " + user_input
    return subprocess.call(cmd, shell=True)

def read_file(path):
    return os.system("cat " + path)

def hash_pw(pw):
    return hashlib.md5(pw.encode()).hexdigest()

# touch to re-trigger review with fixed reusable
