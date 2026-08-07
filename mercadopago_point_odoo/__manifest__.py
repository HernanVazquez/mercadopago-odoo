{
    "name": "Mercado Pago Point",
    "version": "16.0.1.0.0",
    "license": "LGPL-3",
    "depends": ["account"],
    "external_dependencies": {"python": ["requests"]},
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/account_payment_method_data.xml",
        "views/mercadopago_point_config_views.xml",
        "views/mercadopago_point_order_views.xml",
        "views/account_payment_views.xml",
    ],
    "installable": True,
    "application": False,
}
