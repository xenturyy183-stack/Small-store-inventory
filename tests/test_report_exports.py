import csv
import io
import os
import sqlite3
import tempfile
import unittest

from werkzeug.security import generate_password_hash

import app as inventory_app


class ReportExportTests(unittest.TestCase):
    def setUp(self):
        file_descriptor, self.database_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(file_descriptor)
        self.original_database = inventory_app.app.config["DATABASE"]
        self.original_testing = inventory_app.app.config["TESTING"]
        inventory_app.app.config.update(
            DATABASE=self.database_path,
            TESTING=True,
        )
        inventory_app.init_db()

        connection = sqlite3.connect(self.database_path)
        try:
            self.owner_id = connection.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                ("report-owner", generate_password_hash("test-password")),
            ).lastrowid
            self.other_id = connection.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                ("report-other", generate_password_hash("test-password")),
            ).lastrowid
            connection.executemany(
                """
                INSERT INTO items (
                    user_id, name, category, quantity, reorder_level,
                    item_code, unit_cost, price
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (self.owner_id, "Apples", "Fruit", 4, 2, "SKU-000301", 1.5, 3.0),
                    (self.owner_id, "=1+1", "@Unsafe", 2, 1, "SKU-000302", None, 4.0),
                    (self.other_id, "Private item", "Other", 9, 2, "SKU-000303", 2.0, 5.0),
                ],
            )
            connection.executemany(
                """
                INSERT INTO stock_movements (
                    user_id, item_name, movement_type, quantity_change, quantity_after
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                [(self.owner_id, "=2+2", "Stock adjusted", -1, 0) for _ in range(201)]
                + [(self.other_id, "Private item", "Stock added", 1, 10)],
            )
            connection.commit()
        finally:
            connection.close()

        self.owner = self.client_for(self.owner_id)

    def tearDown(self):
        inventory_app.app.config.update(
            DATABASE=self.original_database,
            TESTING=self.original_testing,
        )
        if os.path.exists(self.database_path):
            os.unlink(self.database_path)

    @staticmethod
    def read_csv(response):
        return list(csv.reader(io.StringIO(response.data.decode("utf-8-sig"))))

    def client_for(self, user_id):
        client = inventory_app.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = user_id
        return client

    def test_valuation_export_includes_totals_and_products(self):
        response = self.owner.get("/export/valuation")
        self.assertEqual(response.status_code, 200)
        self.assertIn("valuation-report.csv", response.headers["Content-Disposition"])

        rows = self.read_csv(response)
        total = next(row for row in rows if row[0] == "TOTAL")
        unsafe_category = next(row for row in rows if row[0] == "CATEGORY" and row[1])
        unsafe_product = next(row for row in rows if row[0] == "PRODUCT" and row[3])
        self.assertEqual(total[4:7], ["2", "1", "6"])
        self.assertEqual(total[9:], ["6.0", "20.0"])
        self.assertEqual(unsafe_category[1], "'@Unsafe")
        self.assertEqual(unsafe_product[3], "'=1+1")

    def test_report_pages_link_to_csv_downloads(self):
        valuation_page = self.owner.get("/valuation")
        movement_page = self.owner.get("/movements")
        snapshot_page = self.owner.get("/snapshots")
        self.assertIn(b'href="/export/valuation"', valuation_page.data)
        self.assertIn(b'href="/export/movements"', movement_page.data)
        self.assertIn(b'action="/snapshots"', snapshot_page.data)

    def test_movement_export_includes_full_account_history(self):
        response = self.owner.get("/export/movements")
        self.assertEqual(response.status_code, 200)

        rows = self.read_csv(response)
        self.assertEqual(len(rows), 202)
        self.assertEqual(rows[1][1], "'=2+2")
        self.assertNotIn("Private item", str(rows))

    def test_snapshot_export_preserves_saved_data_and_is_account_scoped(self):
        self.assertEqual(self.owner.get("/snapshots").status_code, 200)
        with self.owner.session_transaction() as session:
            token = session["_csrf_token"]
        self.owner.post("/snapshots", data={"csrf_token": token})

        connection = sqlite3.connect(self.database_path)
        try:
            snapshot_id = connection.execute(
                "SELECT id FROM inventory_snapshots WHERE user_id = ?",
                (self.owner_id,),
            ).fetchone()[0]
            connection.execute(
                "UPDATE items SET quantity = 99 WHERE user_id = ? AND name = 'Apples'",
                (self.owner_id,),
            )
            connection.commit()
        finally:
            connection.close()

        detail_page = self.owner.get("/snapshots/{}".format(snapshot_id))
        self.assertEqual(detail_page.status_code, 200)
        self.assertIn(
            ('href="/snapshots/{}/export"'.format(snapshot_id)).encode(),
            detail_page.data,
        )

        response = self.owner.get("/snapshots/{}/export".format(snapshot_id))
        self.assertEqual(response.status_code, 200)
        rows = self.read_csv(response)
        summary = next(row for row in rows if row[0] == "SNAPSHOT")
        apples = next(row for row in rows if row[0] == "PRODUCT" and row[4] == "Apples")
        self.assertEqual(summary[6:9], ["2", "1", "6"])
        self.assertEqual(apples[9], "4")

        other = self.client_for(self.other_id)
        self.assertEqual(other.get("/snapshots/{}/export".format(snapshot_id)).status_code, 302)

    def test_existing_inventory_export_still_downloads_csv(self):
        response = self.owner.get("/export")
        self.assertEqual(response.status_code, 200)
        self.assertIn("inventory.csv", response.headers["Content-Disposition"])
        rows = self.read_csv(response)
        self.assertEqual(
            rows[0],
            ["item_code", "name", "category", "quantity", "reorder_level", "unit_cost", "price"],
        )


if __name__ == "__main__":
    unittest.main()
