"""Check a digest-qualified Node container's declared health, without host ports."""
import json
import os
import re
import subprocess
import time


def verify(image: str, timeout: int = 90) -> None:
    if not re.fullmatch(r"ghcr\.io/[a-z0-9._/-]+@sha256:[a-f0-9]{64}", image):
        raise ValueError("Health verification requires the exact GHCR digest")
    subprocess.run(["docker", "pull", image], check=True, capture_output=True)
    config = json.loads(subprocess.check_output(["docker", "image", "inspect", image, "--format", "{{json .Config}}"], text=True))
    if not config.get("Healthcheck", {}).get("Test") or config["Healthcheck"]["Test"][0] == "NONE":
        raise ValueError("Node release containers must declare a working HEALTHCHECK")
    container = subprocess.check_output(["docker", "run", "--detach", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--tmpfs", "/tmp:rw,nosuid,nodev,size=16m", image], text=True).strip()
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = json.loads(subprocess.check_output(["docker", "inspect", container, "--format", "{{json .State}}"], text=True))
            health = state.get("Health", {}).get("Status")
            if not state.get("Running") or health == "unhealthy":
                raise ValueError("Candidate stopped or failed its health check")
            if health == "healthy":
                return
            time.sleep(2)
        raise ValueError("Candidate health check timed out")
    finally:
        subprocess.run(["docker", "rm", "--force", container], check=True, capture_output=True)


if __name__ == "__main__":
    verify(os.environ["CANDIDATE_IMAGE"])
