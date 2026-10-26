from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install', 'stock_no_negative')
class TestNegativeStock(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Wh = cls.env['stock.warehouse']
        cls.wh_a = Wh.create({'name': 'NN WH A', 'code': 'NNA'})
        cls.wh_b = Wh.create({'name': 'NN WH B', 'code': 'NNB'})
        cls.categ = cls.env['product.category'].create({'name': 'NN Categ'})
        cls.product = cls.env['product.product'].create({
            'name': 'NN Product', 'type': 'consu', 'is_storable': True,
            'categ_id': cls.categ.id})
        cls.partner = cls.env['res.partner'].create({'name': 'NN Customer'})
        cls.policy = cls.env['stock.negative.policy']
        cls.Quant = cls.env['stock.quant']

    # ---- helpers -------------------------------------------------------
    def _levels(self, wh=None):
        wh = wh or self.wh_a
        return {
            'product': self.product.product_tmpl_id,
            'category': self.categ,
            'warehouse': wh,
            'location': wh.lot_stock_id,
        }

    def _set_all(self, value, wh=None):
        for rec in self._levels(wh).values():
            rec.allow_negative_stock = value

    def _stock(self, wh, qty):
        self.Quant._update_available_quantity(self.product, wh.lot_stock_id, qty)

    def _so(self, wh, *qtys):
        return self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'warehouse_id': wh.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': q})
                           for q in qtys],
        })

    def _qty(self, wh):
        return self.Quant._get_available_quantity(self.product, wh.lot_stock_id)

    # ---- inheritance / resolution -------------------------------------
    def test_default_is_blocking(self):
        self.assertFalse(self.policy.is_negative_allowed(self.product, self.wh_a.lot_stock_id))

    def test_all_levels_true_allows(self):
        self._set_all(True)
        self.assertTrue(self.policy.is_negative_allowed(self.product, self.wh_a.lot_stock_id))

    def test_lower_or_any_level_false_overrides_true(self):
        labels = {'product': 'Product', 'category': 'Product Category',
                  'warehouse': 'Warehouse', 'location': 'Location'}
        for name in labels:
            with self.subTest(level=name):
                self._set_all(True)
                self._levels()[name].allow_negative_stock = False  # also checks cache invalidation
                self.assertFalse(
                    self.policy.is_negative_allowed(self.product, self.wh_a.lot_stock_id))
                self.assertIn(
                    labels[name],
                    self.policy.blocking_levels(self.product, self.wh_a.lot_stock_id))

    def test_category_change_invalidates_cache(self):
        self._set_all(True)
        other = self.env['product.category'].create({'name': 'NN Other'})  # flag False
        self.assertTrue(self.policy.is_negative_allowed(self.product, self.wh_a.lot_stock_id))
        self.product.categ_id = other
        self.assertFalse(self.policy.is_negative_allowed(self.product, self.wh_a.lot_stock_id))

    # ---- quant-level hard guard ---------------------------------------
    def test_quant_guard_blocks_with_clear_message(self):
        self._stock(self.wh_a, 3)
        with self.assertRaises(UserError) as cm:
            self.Quant._update_available_quantity(self.product, self.wh_a.lot_stock_id, -5)
        msg = str(cm.exception)
        self.assertIn(self.product.display_name, msg)
        self.assertIn('Shortfall', msg)
        self.assertEqual(self._qty(self.wh_a), 3)

    def test_exact_consumption_ok(self):
        self._stock(self.wh_a, 3)
        self.Quant._update_available_quantity(self.product, self.wh_a.lot_stock_id, -3)
        self.assertEqual(self._qty(self.wh_a), 0)

    def test_negative_allowed_when_enabled_everywhere(self):
        self._set_all(True)
        self._stock(self.wh_a, 3)
        self.Quant._update_available_quantity(self.product, self.wh_a.lot_stock_id, -5)
        total = sum(self.Quant.search([
            ('product_id', '=', self.product.id),
            ('location_id', '=', self.wh_a.lot_stock_id.id)]).mapped('quantity'))
        self.assertEqual(total, -2)

    # ---- sale order flow ------------------------------------------------
    def test_so_confirm_blocked_with_shortfall(self):
        self._stock(self.wh_a, 3)
        so = self._so(self.wh_a, 5)
        with self.assertRaises(UserError) as cm:
            so.action_confirm()
        self.assertIn(self.product.display_name, str(cm.exception))
        self.assertIn('shortfall', str(cm.exception))
        self.assertEqual(so.state, 'draft')

    def test_so_confirm_ok_when_enough(self):
        self._stock(self.wh_a, 5)
        so = self._so(self.wh_a, 5)
        so.action_confirm()
        self.assertEqual(so.state, 'sale')

    def test_so_lines_are_aggregated(self):
        self._stock(self.wh_a, 5)
        with self.assertRaises(UserError):
            self._so(self.wh_a, 3, 3).action_confirm()

    def test_so_skipped_when_negative_allowed(self):
        self._set_all(True)
        so = self._so(self.wh_a, 5)
        so.action_confirm()
        self.assertEqual(so.state, 'sale')

    # ---- multi-warehouse isolation -------------------------------------
    def test_multi_warehouse_isolation(self):
        self._stock(self.wh_a, 10)
        with self.assertRaises(UserError):
            self._so(self.wh_b, 1).action_confirm()
        with self.assertRaises(UserError):
            self.Quant._update_available_quantity(self.product, self.wh_b.lot_stock_id, -1)
        so = self._so(self.wh_a, 1)
        so.action_confirm()
        self.assertEqual(so.state, 'sale')

    def test_flags_are_scoped_to_their_warehouse(self):
        self._set_all(True, wh=self.wh_a)  # wh_b untouched
        self.assertTrue(self.policy.is_negative_allowed(self.product, self.wh_a.lot_stock_id))
        self.assertFalse(self.policy.is_negative_allowed(self.product, self.wh_b.lot_stock_id))

    # ---- picking validation --------------------------------------------
    def test_picking_validation_cannot_go_negative(self):
        self._stock(self.wh_a, 3)
        customers = self.env.ref('stock.stock_location_customers')
        picking = self.env['stock.picking'].create({
            'picking_type_id': self.wh_a.out_type_id.id,
            'location_id': self.wh_a.lot_stock_id.id,
            'location_dest_id': customers.id,
            'partner_id': self.partner.id,
            'move_ids': [(0, 0, {
                'name': 'nn', 'product_id': self.product.id, 'product_uom_qty': 5,
                'product_uom': self.product.uom_id.id,
                'location_id': self.wh_a.lot_stock_id.id,
                'location_dest_id': customers.id})],
        })
        picking.action_confirm()
        picking.action_assign()
        picking.move_ids.quantity = 5
        picking.move_ids.picked = True
        with self.assertRaises(UserError):
            picking._action_done()
