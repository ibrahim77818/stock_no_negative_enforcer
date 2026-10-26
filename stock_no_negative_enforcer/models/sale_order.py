from collections import defaultdict

from odoo import _, models
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_repr


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    def _negative_stock_must_check(self):
        """Hook: lines served from warehouse stock. Lines with an explicit
        route (dropship, MTO/buy...) are not served from stock and are skipped.
        Override to refine."""
        self.ensure_one()
        return bool(
            not self.display_type and self.product_id
            and self.product_id.type == 'product'
            and not self.route_id
            and not self.is_downpayment
            and self.product_uom_qty > 0)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_confirm(self):
        self._nn_check_stock()
        return super().action_confirm()

    def _nn_check_stock(self):
        policy = self.env['stock.negative.policy']
        quant = self.env['stock.quant']
        digits = self.env['decimal.precision'].precision_get('Product Unit of Measure')
        for order in self:
            warehouse = order.warehouse_id.sudo()
            if not warehouse:
                continue
            needed = defaultdict(float)  # product -> qty in product UoM (aggregated over lines)
            for line in order.order_line:
                if line._negative_stock_must_check():
                    needed[line.product_id] += line.product_uom._compute_quantity(
                        line.product_uom_qty, line.product_id.uom_id)
            # Products for which negative stock is allowed in this warehouse need no check.
            products = [p for p in needed
                        if not policy.is_negative_allowed(p, warehouse.lot_stock_id)]
            if not products:
                continue
            locations = self.env['stock.location'].sudo().search([
                ('id', 'child_of', warehouse.view_location_id.id),
                ('usage', '=', 'internal'),
                ('company_id', 'in', [False, order.company_id.id]),
            ]).ids
            free = {}
            if locations:
                pids = tuple(sorted(p.id for p in products))
                quant.flush_model(['quantity', 'reserved_quantity'])
                # Serialise concurrent confirmations on the same stock; the lock is
                # held until commit, i.e. through the reservation done by super().
                self.env.cr.execute(
                    "SELECT id FROM stock_quant WHERE product_id IN %s AND location_id IN %s "
                    "ORDER BY id FOR UPDATE", (pids, tuple(locations)))
                self.env.cr.execute(
                    "SELECT product_id, COALESCE(SUM(quantity - reserved_quantity), 0) "
                    "FROM stock_quant WHERE product_id IN %s AND location_id IN %s "
                    "GROUP BY product_id", (pids, tuple(locations)))
                free = dict(self.env.cr.fetchall())
            problems = []
            for product in products:
                available = free.get(product.id, 0.0)
                req = needed[product]
                if float_compare(available, req, precision_rounding=product.uom_id.rounding) < 0:
                    problems.append(_(
                        "- %(product)s: requested %(req)s, available %(avail)s, shortfall %(short)s %(uom)s",
                        product=product.display_name,
                        req=float_repr(req, digits),
                        avail=float_repr(max(available, 0.0), digits),
                        short=float_repr(req - max(available, 0.0), digits),
                        uom=product.uom_id.name))
            if problems:
                raise UserError(_(
                    "Cannot confirm %(order)s: not enough stock in warehouse %(wh)s.\n%(lines)s\n"
                    "Negative stock is disabled for these products.",
                    order=order.name, wh=warehouse.name, lines='\n'.join(problems)))
