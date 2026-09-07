"""
Locust load test - targets orders-service (a representative, stateless
write-heavy endpoint). Run for real with:

    pip install locust
    locust -f tests/load/locustfile.py --host http://localhost:8002 \
        --users 20 --spawn-rate 5 --run-time 30s --headless \
        --csv tests/load/results/orders_load

Results are written to tests/load/results/*.csv by Locust itself - do not
hand-edit or fabricate these files; regenerate them by re-running the
command above. See docs/test_plan.md for the actual run's numbers.
"""
from locust import HttpUser, between, task


class OrdersUser(HttpUser):
    wait_time = between(0.1, 0.5)

    @task(3)
    def create_order(self):
        self.client.post("/orders", json={
            "customer": "load-test-customer",
            "item": "widget",
            "quantity": 2,
        })

    @task(1)
    def list_orders(self):
        self.client.get("/orders")

    @task(1)
    def health_check(self):
        self.client.get("/health")
