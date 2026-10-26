from odoo import _, api, models
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_repr


class StockQuant(models.Model):
    _inherit = 'stock.quant'

    @api.model
    def _nn_lock_quants(self, product, location):
        """Pessimistic row lock (FOR UPDATE) on every quant of the pair, in id
        order to keep lock acquisition deterministic."""
        self.flush_model(['quantity', 'reserved_quantity'])
        self.env.cr.execute(
            "SELECT id FROM stock_quant WHERE product_id = %s AND location_id = %s "
            "ORDER BY id FOR UPDATE", (product.id, location.id))

    @api.model
    def _update_available_quantity(self, product_id, location_id, quantity,
                                   lot_id=None, package_id=None, owner_id=None,
                                   in_date=None):
        policy = self.env['stock.negative.policy']
        rounding = product_id.uom_id.rounding
        guard = (
            product_id.type == 'product'
            and location_id.usage == 'internal'
            and float_compare(quantity, 0.0, precision_rounding=rounding) < 0
            and not policy.is_negative_allowed(product_id, location_id)
        )
        if guard:
            self._nn_lock_quants(product_id, location_id)
        res = super()._update_available_quantity(
            product_id, location_id, quantity, lot_id=lot_id,
            package_id=package_id, owner_id=owner_id, in_date=in_date)
        if guard:
            quants = self._gather(product_id, location_id, lot_id=lot_id,
                                  package_id=package_id, owner_id=owner_id,
                                  strict=True)
            total_after = sum(quants.mapped('quantity'))
            if float_compare(total_after, 0.0, precision_rounding=rounding) < 0:
                self._nn_raise_shortfall(product_id, location_id, lot_id,
                                         quantity, total_after)
        return res

    @api.model
    def _nn_raise_shortfall(self, product, location, lot, delta, total_after):
        digits = self.env['decimal.precision'].precision_get('Product Unit of Measure')
        fmt = lambda v: float_repr(v, digits)
        levels = ', '.join(self.env['stock.negative.policy'].blocking_levels(product, location))
        raise UserError(_(
            "Insufficient stock for %(product)s%(lot)s at %(location)s.\n"
            "Requested: %(req)s %(uom)s | Available: %(avail)s %(uom)s | "
            "Shortfall: %(short)s %(uom)s\n"
            "Negative stock is not allowed here (disabled on: %(levels)s).",
            product=product.display_name,
            lot=(" (lot %s)" % lot.name) if lot else "",
            location=location.display_name,
            req=fmt(-delta), avail=fmt(total_after - delta),
            short=fmt(-total_after), uom=product.uom_id.name, levels=levels))
