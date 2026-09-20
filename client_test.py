"""Cliente heredado de TFU 2: 100 solicitudes, 10 workers, hasta 3 intentos."""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

URL = os.environ.get("BASE_URL", "http://localhost:8080").rstrip("/") + "/process"
HEADERS = {"Authorization": "Bearer " + os.environ.get("ADMIN_TOKEN", "TFU-UT2-SECRET-KEY"), "Content-Type": "application/json"}
DATA = json.dumps({"item_id": 101, "name": "ResilienceTest"}).encode()


def request_with_retry(req_id):
    for attempt in range(3):
        try:
            with urlopen(Request(URL, data=DATA, headers=HEADERS), timeout=5) as response:
                print(f"[{req_id:03d}] {json.load(response)['processed_by']}")
                return True
        except HTTPError as error:
            if error.code not in (502, 503, 504):
                return False
        except (URLError, TimeoutError, ConnectionError):
            pass
        if attempt < 2:
            time.sleep(0.25 * 2 ** attempt)
    return False


if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(request_with_retry, range(100)))
    print(f"Éxito: {sum(results)}/{len(results)}")
    raise SystemExit(0 if all(results) else 1)
