from odoo import api, models, tools


class StockNegativePolicy(models.AbstractModel):
    """Resolution engine.

    A boolean cannot distinguish "unset" from "explicitly False", and the spec
    requires False to override True at any upper level. The only consistent
    reading: negative stock is allowed for (product, location) iff EVERY
    applicable level is True. Any False level blocks. Results are ormcached
    per (product, location) and invalidated by the config mixin.
    """
    _name = 'stock.negative.policy'
    _description = 'Negative Stock Resolution Engine'

    @api.model
    @tools.ormcache('product_id', 'location_id')
    def _blocking_levels(self, product_id, location_id):
        product = self.env['product.product'].sudo().browse(product_id)
        location = self.env['stock.location'].sudo().browse(location_id)
        warehouse = location.warehouse_id  # the warehouse owning this location
        blocking = []
        if not product.product_tmpl_id.allow_negative_stock:
            blocking.append('Product')
        if not product.categ_id.allow_negative_stock:
            blocking.append('Product Category')
        if not location.allow_negative_stock:
            blocking.append('Location')
        if warehouse and not warehouse.allow_negative_stock:
            blocking.append('Warehouse')
        return tuple(blocking)

    @api.model
    def blocking_levels(self, product, location):
        return self._blocking_levels(product.id, location.id)

    @api.model
    def is_negative_allowed(self, product, location):
        return not self._blocking_levels(product.id, location.id)
