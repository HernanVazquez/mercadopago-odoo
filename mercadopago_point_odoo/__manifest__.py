{
    "name": "Mercado Pago Point",
    "version": "16.0.2.5.0",
    "license": "LGPL-3",
    "depends": ["account", "web"],
    "external_dependencies": {"python": ["requests"]},
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/account_payment_method_data.xml",
        "views/mercadopago_point_config_views.xml",
        "views/mercadopago_point_order_views.xml",
        "views/mercadopago_point_tracking_views.xml",
        "views/account_payment_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "mercadopago_point_odoo/static/src/js/point_tracking.js",
            "mercadopago_point_odoo/static/src/xml/point_tracking.xml",
            "mercadopago_point_odoo/static/src/scss/point_tracking.scss",
        ],
        "web.qunit_suite_tests": [
            "mercadopago_point_odoo/static/tests/point_tracking_tests.js",
        ],
    },
    "installable": True,
    "application": False,
}
