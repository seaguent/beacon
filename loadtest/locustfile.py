import random

from locust import HttpUser, task, between


class BeaconUser(HttpUser):
    wait_time = between(0.1, 0.3)

    @task
    def send_event(self):
        self.client.post(
            "/events",
            json={
                "target_url": "http://fake-receiver:9000/webhook",
                "payload": {"order_id": random.randint(1, 1_000_000)},
            },
        )
