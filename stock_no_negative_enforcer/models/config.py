from odoo import fields, models


class NegativeStockMixin(models.AbstractModel):
    """Adds the ``allow_negative_stock`` toggle and invalidates the
    resolution cache whenever a relevant field changes."""
    _name = 'stock.negative.mixin'
    _description = 'Negative Stock Toggle Mixin'

    # Extra fields (besides the flag) whose change alters the resolution.
    _nn_invalidating_fields = frozenset()

    allow_negative_stock = fields.Boolean(
        string='Allow Negative Stock', default=False,
        help="Negative stock is only possible when this flag is enabled on "
             "EVERY applicable level (Product, Product Category, Location, "
             "Warehouse). A disabled level always overrides enabled ones.")

    def write(self, vals):
        res = super().write(vals)
        if 'allow_negative_stock' in vals or self._nn_invalidating_fields.intersection(vals):
            self._nn_invalidate_cache()
        return res

    def _nn_invalidate_cache(self):
        """Clear the resolution cache (and signal the other workers).

        Cache invalidation goes through the registry (decorated methods no
        longer expose ``clear_cache`` and ``Model.clear_caches()`` is gone).
        """
        self.env.registry.clear_cache()


class StockWarehouse(models.Model):
    _name = 'stock.warehouse'
    _inherit = ['stock.warehouse', 'stock.negative.mixin']
    _nn_invalidating_fields = frozenset(['view_location_id', 'lot_stock_id'])


class StockLocation(models.Model):
    _name = 'stock.location'
    _inherit = ['stock.location', 'stock.negative.mixin']
    _nn_invalidating_fields = frozenset(['location_id', 'usage'])


class ProductCategory(models.Model):
    _name = 'product.category'
    _inherit = ['product.category', 'stock.negative.mixin']


class ProductTemplate(models.Model):
    _name = 'product.template'
    _inherit = ['product.template', 'stock.negative.mixin']
    _nn_invalidating_fields = frozenset(['categ_id'])
