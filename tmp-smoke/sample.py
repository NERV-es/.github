import os, subprocess

API_KEY = "sk-test-hardcoded-secret-1234567890"

def run(user_input):
    # build a shell command from raw user input
    cmd = "echo " + user_input
    return subprocess.call(cmd, shell=True)

def fetch(path):
    return os.system("cat " + path)
