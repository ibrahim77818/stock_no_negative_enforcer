import random
import threading
import time

import psycopg2
from psycopg2 import errorcodes

from odoo import SUPERUSER_ID, api
from odoo.exceptions import UserError
from odoo.modules.registry import Registry
from odoo.tests import common, tagged


@tagged('post_install', '-at_install', 'stock_no_negative')
class TestConcurrentNegativeStock(common.BaseCase):
    """Real concurrency: independent PostgreSQL connections, committed fixture.
    N workers each try to ship QTY units from STOCK units. Exactly
    STOCK // QTY may succeed; the rest must be blocked; stock never < 0.
    Run on a throwaway database (the fixture is committed, then cleaned up)."""

    N_WORKERS, STOCK, QTY = 8, 10, 3

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.reg = Registry(common.get_db_name())
        with cls.reg.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            product = env['product.product'].create({
                'name': 'NN Concurrent Product', 'type': 'consu', 'is_storable': True})
            location = env.ref('stock.warehouse0').lot_stock_id
            env['stock.quant']._update_available_quantity(product, location, cls.STOCK)
            cls.product_id, cls.location_id = product.id, location.id

    @classmethod
    def tearDownClass(cls):
        with cls.reg.cursor() as cr:
            cr.execute("DELETE FROM stock_quant WHERE product_id = %s", (cls.product_id,))
            env = api.Environment(cr, SUPERUSER_ID, {})
            env['product.product'].browse(cls.product_id).unlink()
        super().tearDownClass()

    def _worker(self, barrier, results):
        barrier.wait()
        for _attempt in range(15):
            try:
                with self.reg.cursor() as cr:
                    env = api.Environment(cr, SUPERUSER_ID, {})
                    env['stock.quant']._update_available_quantity(
                        env['product.product'].browse(self.product_id),
                        env['stock.location'].browse(self.location_id), -self.QTY)
                results.append('ok')
                return
            except UserError:
                results.append('blocked')
                return
            except psycopg2.OperationalError as exc:
                # Same retry Odoo's RPC layer performs on concurrency errors.
                if exc.pgcode in (errorcodes.SERIALIZATION_FAILURE, errorcodes.DEADLOCK_DETECTED):
                    time.sleep(random.random() * 0.05)
                    continue
                raise
        results.append('gave_up')

    def test_no_negative_stock_under_race(self):
        barrier = threading.Barrier(self.N_WORKERS)
        results = []
        threads = [threading.Thread(target=self._worker, args=(barrier, results))
                   for _i in range(self.N_WORKERS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
        ok = results.count('ok')
        self.assertEqual(len(results), self.N_WORKERS)
        self.assertNotIn('gave_up', results)
        self.assertEqual(ok, self.STOCK // self.QTY)
        self.assertEqual(results.count('blocked'), self.N_WORKERS - ok)
        with self.reg.cursor() as cr:
            cr.execute("SELECT COALESCE(SUM(quantity), 0) FROM stock_quant "
                       "WHERE product_id = %s AND location_id = %s",
                       (self.product_id, self.location_id))
            final = cr.fetchone()[0]
        self.assertGreaterEqual(final, 0)
        self.assertEqual(final, self.STOCK - self.QTY * ok)
