"""Pruebas de integración contra Docker. Solo requieren Python estándar en el host.
Cada prueba crea datos propios y los conserva para inspección en la demo.
"""
import base64
import json
import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080").rstrip("/")
ADMIN = os.environ.get("ADMIN_TOKEN", "TFU-UT2-SECRET-KEY")


def api(method, path, body=None, token=ADMIN):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    req = Request(BASE_URL + path, data=None if body is None else json.dumps(body).encode(), headers=headers, method=method)
    try:
        with urlopen(req, timeout=20) as response:
            return response.status, json.load(response), response.headers.get("X-Replica-ID")
    except HTTPError as error:
        return error.code, json.load(error), error.headers.get("X-Replica-ID")


def operation(**kwargs):
    return dict(operation_id=str(uuid.uuid4()), **kwargs)


class AccesoRodoTests(unittest.TestCase):
    def ok(self, method, path, data=None, token=ADMIN, status=200):
        code, result, _ = api(method, path, data, token)
        self.assertEqual(code, status, result)
        return result

    def fixture(self, balance="100.00", price="10.00", capacity=3):
        email = str(uuid.uuid4()) + "@demo.test"
        user = self.ok("POST", "/users/", {"name": "Visitante demo", "email": email, "password": "Demo-12345"}, token=None, status=201)
        login = self.ok("POST", "/users/login", {"email": email, "password": "Demo-12345"}, token=None)
        token = login["access_token"]
        card = self.ok("POST", "/cards/", {"user_id": user["id"]}, token, status=201)
        if Decimal(balance):
            self.ok("PATCH", f'/cards/{card["id"]}/recharge', operation(amount=balance))
        ride = self.ok("POST", "/rides/", {"name": "Rueda demo", "price": price, "capacity": capacity}, status=201)
        return user, card, ride, token

    def test_01_security_and_validation(self):
        self.assertEqual(api("POST", "/process", {"item_id": 1, "name": "Test"}, None)[0], 401)
        self.assertEqual(api("POST", "/process", {"item_id": 1, "name": "Test"}, "invalid")[0], 401)
        malformed = base64.urlsafe_b64encode("1:9999999999:á".encode()).decode()
        self.assertEqual(api("POST", "/process", {"item_id": 1, "name": "Test"}, malformed)[0], 401)
        self.assertEqual(api("POST", "/process", {"item_id": -1, "name": "x"})[0], 422)
        self.assertEqual(api("POST", "/rides/", {"name": "Test", "price": "1.001", "capacity": 1})[0], 422)
        user, card, ride, token = self.fixture()
        self.assertEqual(api("PATCH", f'/cards/{card["id"]}/recharge', operation(amount=10), token)[0], 403)
        self.assertEqual(api("POST", "/cards/", {"user_id": 9223372036854775807})[0], 404)
        other, _, _, other_token = self.fixture()
        self.assertEqual(api("GET", f'/cards/{card["id"]}', token=other_token)[0], 403)
        self.assertEqual(api("GET", f'/cards/{card["id"]}/history', token=other_token)[0], 403)
        self.ok("DELETE", f'/users/{user["id"]}', token=token)
        self.assertEqual(api("GET", f'/cards/{card["id"]}', token=token)[0], 401)

    def test_02_shared_state_and_token_across_replicas(self):
        _, card, _, token = self.fixture()
        replies = [api("GET", f'/cards/{card["id"]}', token=token) for _ in range(12)]
        self.assertTrue(all(code == 200 and Decimal(str(body["balance"])) == 100 for code, body, _ in replies))
        replicas = {replica for _, _, replica in replies}
        self.assertEqual(len(replicas), 3, f"Se esperan tres réplicas: {replicas}")
        print("\nRéplicas con el mismo saldo y token:", sorted(replicas))

    def test_03_recharge_and_access_idempotency(self):
        _, card, ride, _ = self.fixture(balance="0")
        recharge = operation(amount="100.00")
        path = f'/cards/{card["id"]}/recharge'
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: api("PATCH", path, recharge), range(8)))
        self.assertTrue(all(code == 200 for code, _, _ in results), results)
        self.assertTrue(all(body == results[0][1] for _, body, _ in results))
        changed = dict(recharge, amount="200.00")
        self.assertEqual(api("PATCH", path, changed)[0], 409)
        access = operation(card_id=card["id"], ride_id=ride["id"])
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: api("POST", "/access/validate", access), range(8)))
        self.assertTrue(all(code == 200 for code, _, _ in results), results)
        self.assertTrue(all(body == results[0][1] for _, body, _ in results))
        self.assertEqual(Decimal(str(self.ok("GET", f'/cards/{card["id"]}')["balance"])), 90)
        self.assertEqual(self.ok("GET", f'/rides/{ride["id"]}')["current_occupancy"], 1)
        self.assertEqual(len(self.ok("GET", f'/cards/{card["id"]}/history')["history"]), 2)

    def test_04_rollback_after_reservation(self):
        _, card, ride, _ = self.fixture(balance="0")
        data = operation(card_id=card["id"], ride_id=ride["id"])
        self.assertEqual(api("POST", "/access/validate", data)[0], 403)
        self.assertEqual(self.ok("GET", f'/rides/{ride["id"]}')["current_occupancy"], 0)
        self.assertEqual(self.ok("GET", f'/cards/{card["id"]}/history')["history"], [])
        self.assertEqual(Decimal(str(self.ok("GET", f'/cards/{card["id"]}')["balance"])), 0)
        # La operación fallida no quedó confirmada: se puede repetir tras recargar.
        self.ok("PATCH", f'/cards/{card["id"]}/recharge', operation(amount=10))
        self.ok("POST", "/access/validate", data)

    def test_05_concurrent_capacity_with_distinct_cards(self):
        user, card, ride, _ = self.fixture(capacity=1)
        second = self.ok("POST", "/cards/", {"user_id": user["id"]}, status=201)
        self.ok("PATCH", f'/cards/{second["id"]}/recharge', operation(amount=100))
        ids = [card["id"], second["id"]]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda cid: api("POST", "/access/validate", operation(card_id=cid, ride_id=ride["id"])), ids))
        self.assertEqual(sorted(code for code, _, _ in results), [200, 403])
        self.assertEqual(self.ok("GET", f'/rides/{ride["id"]}')["current_occupancy"], 1)
        balances = [Decimal(str(self.ok("GET", f'/cards/{cid}')["balance"])) for cid in ids]
        self.assertEqual(sorted(balances), [Decimal(90), Decimal(100)])

    def test_06_concurrent_balance(self):
        _, card, ride, _ = self.fixture(balance="10", capacity=20)
        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(lambda _: api("POST", "/access/validate", operation(card_id=card["id"], ride_id=ride["id"])), range(10)))
        self.assertEqual(sum(code == 200 for code, _, _ in results), 1, results)
        self.assertEqual(sum(code == 403 for code, _, _ in results), 9, results)
        self.assertEqual(Decimal(str(self.ok("GET", f'/cards/{card["id"]}')["balance"])), 0)
        self.assertEqual(self.ok("GET", f'/rides/{ride["id"]}')["current_occupancy"], 1)

    def test_07_finish_round_and_rejected_update(self):
        _, card, ride, _ = self.fixture(capacity=3)
        for _ in range(2):
            self.ok("POST", "/access/validate", operation(card_id=card["id"], ride_id=ride["id"]))
        self.assertEqual(api("PATCH", f'/rides/{ride["id"]}', {"name": "Cambio rechazado", "price": "20", "capacity": 1})[0], 409)
        current = self.ok("GET", f'/rides/{ride["id"]}')
        self.assertEqual(current["name"], ride["name"])
        self.assertEqual(Decimal(str(current["price"])), 10)
        finish = operation()
        self.ok("POST", f'/rides/{ride["id"]}/finish-round', finish)
        self.ok("POST", "/access/validate", operation(card_id=card["id"], ride_id=ride["id"]))
        self.ok("POST", f'/rides/{ride["id"]}/finish-round', finish)
        self.assertEqual(self.ok("GET", f'/rides/{ride["id"]}')["current_occupancy"], 1)

    def test_08_out_of_service_and_inactive_card(self):
        _, card, ride, _ = self.fixture()
        self.ok("PATCH", f'/rides/{ride["id"]}/status', {"status": "out_of_service"})
        self.assertEqual(api("POST", "/access/validate", operation(card_id=card["id"], ride_id=ride["id"]))[0], 403)
        self.ok("PATCH", f'/rides/{ride["id"]}/status', {"status": "active"})
        self.ok("DELETE", f'/cards/{card["id"]}')
        self.assertEqual(api("POST", "/access/validate", operation(card_id=card["id"], ride_id=ride["id"]))[0], 403)
        self.assertEqual(api("PATCH", f'/cards/{card["id"]}/recharge', operation(amount=10))[0], 403)

    def test_09_login_lock_persists(self):
        email = str(uuid.uuid4()) + "@demo.test"
        self.ok("POST", "/users/", {"name": "Login demo", "email": email, "password": "Demo-12345"}, status=201)
        for _ in range(5):
            self.assertEqual(api("POST", "/users/login", {"email": email, "password": "incorrecta"}, None)[0], 401)
        code, body, _ = api("POST", "/users/login", {"email": email, "password": "Demo-12345"}, None)
        self.assertEqual(code, 401)
        self.assertIn("bloqueada", body["detail"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
